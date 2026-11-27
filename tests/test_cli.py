import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from helpers import CountingEmbedder, HashEmbedder
from telltale import cli
from telltale.answer.brief import CUT_OFF, CUT_OFF_EMPTY, NO_ANSWER, REFUSED
from telltale.ingest.download import DownloadError
from telltale.ingest.store import Store
from telltale.models import Answer, Chunk, Citation, ReportMeta, Sentence
from telltale.search import EmbedderUnavailable, HybridIndex
from telltale.search.embed import DEFAULT_MODEL
from telltale.settings import Settings
from telltale.sources.maib import SkippedReport

PDF_URL = "https://assets.publishing.service.gov.uk/media/test/report-alpha.pdf"


def write_text_pdf(path: Path, text: str) -> None:
    """A one-page PDF whose page text is `text` (Helvetica, no images)."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=200)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as f:
        writer.write(f)


def report(slug: str, pdf_url: str | None) -> ReportMeta:
    return ReportMeta(
        slug=slug,
        title=f"Test report {slug}",
        page_url=f"https://www.gov.uk/maib-reports/{slug}",
        published="2026-03-12T10:15:00.000+00:00",
        occurred=None,
        vessel_types=["merchant-vessel-100-gross-tons-or-over"],
        report_type="investigation-report",
        pdf_url=pdf_url,
    )


def stub_answer(
    sentences: list[Sentence], stop_reason: str = "end_turn", chunks: list[Chunk] | None = None
):
    def fake(question, index, client, model):
        return Answer(
            question=question,
            sentences=sentences,
            chunks=chunks or [],
            input_tokens=20_000,
            output_tokens=1_300,
            cost_usd=0.00265,
            stop_reason=stop_reason,
        )

    return fake


SOURCED = "The bosun entered the ballast tank without a gas test."
CITATION = Citation(
    report_title="Test report alpha",
    source=f"{PDF_URL}#page=4",
    cited_text="The bosun entered the tank without a gas test.",
)


@pytest.fixture
def ask_offline(monkeypatch, tmp_path: Path):
    """`telltale ask` with a fake key, no index and no network; set `cli.answer` per test."""
    settings = Settings(anthropic_api_key="test-key", data_dir=tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda *a, **k: settings)
    monkeypatch.setattr(cli, "load_index", lambda data_dir: object())


def test_ask_prints_no_source_marker(ask_offline, monkeypatch, capsys):
    monkeypatch.setattr(cli, "answer", stub_answer([
        Sentence(text=SOURCED, citations=[CITATION]),
        Sentence(text="Gas tests are always required before entry.", citations=[]),
    ]))

    assert cli.main(["ask", "entering a ballast tank"]) == 0

    out = capsys.readouterr().out
    assert out.count("[no source]") == 1
    assert f"1. {SOURCED} [1]" in out
    assert "2. Gas tests are always required before entry. [no source]" in out
    sources = out.split("Sources:")[1].split("Cost:")[0]
    entries = [line for line in sources.splitlines() if line.startswith("[")]
    assert entries == ["[1] Test report alpha, page 4. Crown copyright."]
    assert f"{PDF_URL}#page=4" in sources
    assert "Cost: $0.0027 (20,000 in / 1,300 out)" in out


def test_ask_prints_no_answer_plainly(ask_offline, monkeypatch, capsys):
    monkeypatch.setattr(cli, "answer", stub_answer([Sentence(text=NO_ANSWER, citations=[])]))

    assert cli.main(["ask", "replace a crankcase relief valve"]) == 0

    out = capsys.readouterr().out
    assert out.startswith(NO_ANSWER + "\n")
    assert "[no source]" not in out
    assert "Sources:" not in out


@pytest.mark.parametrize(
    ("stop_reason", "message"), [("refusal", REFUSED), ("max_tokens", CUT_OFF_EMPTY)]
)
def test_ask_says_refused_or_cut_off_instead_of_no_answer(
    ask_offline, monkeypatch, capsys, stop_reason, message
):
    monkeypatch.setattr(
        cli, "answer", stub_answer([Sentence(text=NO_ANSWER, citations=[])], stop_reason)
    )

    cli.main(["ask", "a question"])

    out = capsys.readouterr().out
    assert message in out
    assert NO_ANSWER not in out


def test_ask_flags_a_cut_off_answer(ask_offline, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "answer", stub_answer([Sentence(text=SOURCED, citations=[CITATION])], "max_tokens")
    )

    cli.main(["ask", "a question"])

    assert CUT_OFF in capsys.readouterr().out


def test_ask_without_key_is_friendly(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here

    assert cli.main(["ask", "a question"]) == 1

    captured = capsys.readouterr()
    assert "ANTHROPIC_API_KEY is not set" in captured.err
    assert "Traceback" not in captured.err + captured.out


def test_ask_without_ingest_is_friendly(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["ask", "a question"]) == 1
    assert "Run `telltale ingest` first." in capsys.readouterr().err
    # Asking never creates an empty store.
    assert not (tmp_path / "data").exists()


def test_ask_with_stale_index_is_friendly(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "embedder", lambda data_dir: HashEmbedder())
    with Store(tmp_path / "data" / "telltale.sqlite") as store:
        store.upsert_report(report("test-report-alpha", PDF_URL))
        store.replace_chunks("test-report-alpha", [(1, ["The line parted."])])

    assert cli.main(["ask", "a question"]) == 1
    err = capsys.readouterr().err
    assert "missing or out of date" in err
    assert "Run `telltale ingest` first." in err


def test_ingest_survives_a_failing_report_and_rebuilds_the_index(
    monkeypatch, tmp_path: Path, capsys
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # ingest needs no key
    monkeypatch.chdir(tmp_path)
    alpha = report("test-report-alpha", PDF_URL)
    bravo = report("test-report-bravo", "https://assets.publishing.service.gov.uk/media/test/b.pdf")
    charlie = report("test-report-charlie", None)
    delta = report("test-report-delta", "https://assets.publishing.service.gov.uk/media/test/d.pdf")

    def fake_list(client, limit=None, on_skip=None):
        page = "https://www.gov.uk/maib-reports/"
        on_skip(SkippedReport(
            "test-report-echo", "Test report echo", page + "test-report-echo",
            "no report type", dropped=True,
        ))
        on_skip(SkippedReport(
            "test-overseas", "Test overseas report", page + "test-overseas",
            "overseas-report", dropped=False,
        ))
        return [alpha, bravo, charlie, delta]

    def fake_download(client, meta, dest):
        if meta.pdf_url is None:
            return None
        if meta is bravo:
            raise DownloadError(f"{meta.pdf_url} did not return a PDF")
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"{meta.slug}.pdf"
        if meta is delta:
            path.write_bytes(b"%PDF-1.7\nnot really a pdf")
        else:
            write_text_pdf(path, "The mooring line parted during berthing.")
        return path

    monkeypatch.setattr(cli, "list_merchant_reports", fake_list)
    monkeypatch.setattr(cli, "download_pdf", fake_download)
    monkeypatch.setattr(cli, "embedder", lambda data_dir: HashEmbedder())

    assert cli.main(["ingest", "--limit", "4"]) == 0

    out = capsys.readouterr().out
    data = tmp_path / "data"
    # The good report after the failing one was still stored, and the index was rebuilt.
    with Store(data / "telltale.sqlite") as store:
        chunks = store.chunks()
    assert [c.report_slug for c in chunks] == ["test-report-alpha"]
    assert isinstance(chunks[0], Chunk)
    index = HybridIndex.load(data / "index", chunks, HashEmbedder())
    assert [c.report_slug for c in index.search("mooring line parted")] == ["test-report-alpha"]

    assert "Reports:                 4" in out
    assert "Reports with a PDF link: 3" in out
    assert "Page groups (chunks):    1" in out
    no_pdf = out.split("Reports with no PDF link")[1].split("\n\n")[0]
    assert "https://www.gov.uk/maib-reports/test-report-charlie" in no_pdf
    no_text = out.split("Reports with a PDF link but no text")[1].split("\n\n")[0]
    assert "(2):" in no_text
    assert "https://www.gov.uk/maib-reports/test-report-bravo" in no_text
    assert "https://www.gov.uk/maib-reports/test-report-delta" in no_text
    errors = out.split("Errors this run")[1].split("\n\n")[0]
    assert "(2):" in errors
    assert "did not return a PDF" in errors
    assert "PDF could not be read" in errors
    dropped = out.split("Left out of the listing because they could not be read")[1]
    assert "(1):" in dropped
    assert "https://www.gov.uk/maib-reports/test-report-echo" in dropped
    assert "Out of scope, left out:\n  MAIB: 1 (overseas-report 1)" in out
    assert "    investigation-report: " in out


def test_ingest_listing_failure_is_friendly(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.chdir(tmp_path)

    def failing_list(client, limit=None, on_skip=None):
        request = httpx.Request("GET", "https://www.gov.uk/api/search.json")
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(cli, "list_merchant_reports", failing_list)

    assert cli.main(["ingest"]) == 1
    err = capsys.readouterr().err
    assert "Could not list MAIB reports: ConnectError" in err
    assert "Traceback" not in err


def test_ask_marks_a_no_answer_sentence_that_makes_a_claim(ask_offline, monkeypatch, capsys):
    mixed = (
        "There is no reliable answer in these reports, but a gas test must show 20.9% oxygen "
        "before entry."
    )
    monkeypatch.setattr(cli, "answer", stub_answer([Sentence(text=mixed, citations=[])]))

    cli.main(["ask", "entering a ballast tank"])

    assert f"1. {mixed} [no source]" in capsys.readouterr().out


class BrokenEmbedder:
    def embed(self, texts):
        raise EmbedderUnavailable("embedding model test/model could not be loaded: offline")


def test_ingest_prints_the_lists_before_an_index_failure(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.chdir(tmp_path)
    alpha = report("test-report-alpha", PDF_URL)
    charlie = report("test-report-charlie", None)

    def fake_download(client, meta, dest):
        if meta.pdf_url is None:
            return None
        dest.mkdir(parents=True, exist_ok=True)
        write_text_pdf(dest / "a.pdf", "The mooring line parted during berthing.")
        return dest / "a.pdf"

    monkeypatch.setattr(cli, "list_merchant_reports", lambda *a, **k: [alpha, charlie])
    monkeypatch.setattr(cli, "download_pdf", fake_download)
    monkeypatch.setattr(cli, "embedder", lambda data_dir: BrokenEmbedder())

    assert cli.main(["ingest"]) == 1

    captured = capsys.readouterr()
    assert "Reports:                 2" in captured.out
    assert "https://www.gov.uk/maib-reports/test-report-charlie" in captured.out
    assert "Search index saved" not in captured.out
    assert "The search index was not built" in captured.err
    assert "with internet access" in captured.err
    assert "Traceback" not in captured.out + captured.err
    # The stored reports survive the failed index build.
    with Store(tmp_path / "data" / "telltale.sqlite") as store:
        assert store.counts()["chunks"] == 1


def test_ask_with_model_unavailable_offline_is_friendly(monkeypatch, tmp_path: Path, capsys):
    """The real FastEmbedder, with a fastembed whose model load fails as it would offline."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)

    class OfflineTextEmbedding:
        def __init__(self, model_name, cache_dir=None):
            raise OSError("could not reach the model host")

    monkeypatch.setitem(
        sys.modules, "fastembed", SimpleNamespace(TextEmbedding=OfflineTextEmbedding)
    )

    class SavedWithRealName(HashEmbedder):
        model = DEFAULT_MODEL

    with Store(tmp_path / "data" / "telltale.sqlite") as store:
        store.upsert_report(report("test-report-alpha", PDF_URL))
        store.replace_chunks("test-report-alpha", [(1, ["The mooring line parted."])])
        HybridIndex.build(store.chunks(), SavedWithRealName()).save(tmp_path / "data" / "index")

    assert cli.main(["ask", "mooring line"]) == 1

    captured = capsys.readouterr()
    assert cli.MODEL_OFFLINE in captured.err
    assert "Traceback" not in captured.out + captured.err


@pytest.fixture
def no_uvicorn(monkeypatch):
    """`telltale serve` never starts a real server in tests; the call is recorded instead."""
    calls: list[dict] = []

    def fake_run(app, **kwargs):
        calls.append({"app": app, **kwargs})

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    return calls


def test_serve_without_index_is_friendly(no_uvicorn, monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["serve"]) == 1

    captured = capsys.readouterr()
    assert "Run `telltale ingest` first." in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert no_uvicorn == []


def test_serve_with_stale_index_is_friendly(no_uvicorn, monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "embedder", lambda data_dir: HashEmbedder())
    with Store(tmp_path / "data" / "telltale.sqlite") as store:
        store.upsert_report(report("test-report-alpha", PDF_URL))
        store.replace_chunks("test-report-alpha", [(1, ["The line parted."])])

    assert cli.main(["serve"]) == 1
    assert "missing or out of date" in capsys.readouterr().err
    assert no_uvicorn == []


def test_serve_without_key_is_friendly(no_uvicorn, monkeypatch, tmp_path: Path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here

    assert cli.main(["serve"]) == 1

    captured = capsys.readouterr()
    assert "ANTHROPIC_API_KEY is not set" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert no_uvicorn == []


def test_serve_with_model_unavailable_offline_is_friendly(
    no_uvicorn, monkeypatch, tmp_path: Path, capsys
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)

    class NamedHashEmbedder(HashEmbedder):
        model = "test-hash"

    class OfflineEmbedder(NamedHashEmbedder):
        def embed(self, texts):
            raise EmbedderUnavailable("could not reach the model host")

    with Store(tmp_path / "data" / "telltale.sqlite") as store:
        store.upsert_report(report("test-report-alpha", PDF_URL))
        store.replace_chunks("test-report-alpha", [(1, ["The mooring line parted."])])
        HybridIndex.build(store.chunks(), NamedHashEmbedder()).save(tmp_path / "data" / "index")
    monkeypatch.setattr(cli, "embedder", lambda data_dir: OfflineEmbedder())

    assert cli.main(["serve"]) == 1

    captured = capsys.readouterr()
    assert cli.MODEL_OFFLINE in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert no_uvicorn == []


def test_serve_starts_the_page_with_the_loaded_index(no_uvicorn, monkeypatch, tmp_path: Path):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "embedder", lambda data_dir: HashEmbedder())
    with Store(tmp_path / "data" / "telltale.sqlite") as store:
        store.upsert_report(report("test-report-alpha", PDF_URL))
        store.replace_chunks("test-report-alpha", [(4, ["The bosun entered the tank."])])
        HybridIndex.build(store.chunks(), HashEmbedder()).save(tmp_path / "data" / "index")
    asked = []

    def fake_answer(question, index, client, model):
        asked.append((question, type(index).__name__, model))
        return stub_answer([Sentence(text=SOURCED, citations=[CITATION])])(
            question, index, client, model
        )

    monkeypatch.setattr(cli, "answer", fake_answer)

    assert cli.main(["serve", "--host", "0.0.0.0", "--port", "8123"]) == 0

    [call] = no_uvicorn
    assert (call["host"], call["port"]) == ("0.0.0.0", 8123)
    page = TestClient(call["app"]).post("/ask", data={"question": "tank entry"})
    assert SOURCED in page.text
    assert asked == [("tank entry", "HybridIndex", "claude-haiku-5-5")]


def test_serve_defaults_to_this_computer_only(no_uvicorn, monkeypatch):
    settings = Settings(anthropic_api_key="test-key")
    monkeypatch.setattr(cli, "load_settings", lambda *a, **k: settings)
    monkeypatch.setattr(cli, "load_index", lambda data_dir: SimpleNamespace(search=lambda q, k: []))

    assert cli.main(["serve"]) == 0

    [call] = no_uvicorn
    assert (call["host"], call["port"]) == ("127.0.0.1", 8000)


@pytest.mark.parametrize(
    ("question", "message"),
    [
        ("", "Type the job you are about to do."),
        ("   \n\t", "Type the job you are about to do."),
        ("​?!", "Type the job you are about to do."),
        ("x" * 501, "Please keep it under 500 characters."),
    ],
    ids=["empty", "spaces", "no-word", "too-long"],
)
def test_ask_checks_the_question_like_the_page(monkeypatch, capsys, question, message):
    def no_call(*args, **kwargs):
        raise AssertionError("no settings, index or model call for a bad question")

    monkeypatch.setattr(cli, "load_settings", no_call)
    monkeypatch.setattr(cli, "load_index", no_call)
    monkeypatch.setattr(cli, "answer", no_call)

    assert cli.main(["ask", question]) == 1

    captured = capsys.readouterr()
    assert captured.err.strip() == message
    assert "no reliable answer" not in captured.out + captured.err


def test_ask_sends_the_trimmed_question(ask_offline, monkeypatch, capsys):
    asked: list[str] = []

    def fake(question, index, client, model):
        asked.append(question)
        return stub_answer([Sentence(text=NO_ANSWER, citations=[])])(question, index, client, model)

    monkeypatch.setattr(cli, "answer", fake)

    assert cli.main(["ask", "  " + "x" * 500 + "\n"]) == 0
    assert asked == ["x" * 500]


class BuggyEmbedder:
    def embed(self, texts):
        raise ValueError("a bug in the index build")


def stored_chunk(data: Path) -> None:
    """A store holding one searchable report from an earlier run."""
    with Store(data / "telltale.sqlite") as store:
        store.upsert_report(report("test-report-alpha", PDF_URL))
        store.replace_chunks("test-report-alpha", [(1, ["The mooring line parted."])])


def test_ingest_index_bug_is_logged_with_its_traceback(
    monkeypatch, tmp_path: Path, capsys, caplog
):
    monkeypatch.chdir(tmp_path)
    stored_chunk(tmp_path / "data")

    def fake_list(client, limit=None, on_skip=None):
        on_skip(SkippedReport("", "search result 4", "", "search result has no link", True))
        return [report("test-report-charlie", None)]

    monkeypatch.setattr(cli, "list_merchant_reports", fake_list)
    monkeypatch.setattr(cli, "embedder", lambda data_dir: BuggyEmbedder())

    with caplog.at_level("WARNING", logger="telltale.cli"):
        assert cli.main(["ingest"]) == 1

    captured = capsys.readouterr()
    assert "The search index was not built: ValueError: a bug in the index build" in captured.err
    records = [r for r in caplog.records if r.name == "telltale.cli" and r.exc_info]
    assert len(records) == 1
    assert records[0].levelname == "WARNING"
    assert isinstance(records[0].exc_info[1], ValueError)
    # A dropped search result with no link is still listed, without a page link.
    dropped = captured.out.split("Left out of the listing because they could not be read")[1]
    assert "MAIB (1):\n    - search result 4: search result has no link\n      no page link" in dropped


def test_ingest_offline_index_failure_logs_no_traceback(monkeypatch, tmp_path: Path, caplog):
    monkeypatch.chdir(tmp_path)
    stored_chunk(tmp_path / "data")
    monkeypatch.setattr(cli, "list_merchant_reports", lambda *a, **k: [])
    monkeypatch.setattr(cli, "embedder", lambda data_dir: BrokenEmbedder())

    with caplog.at_level("WARNING", logger="telltale.cli"):
        assert cli.main(["ingest"]) == 1

    assert not [r for r in caplog.records if r.exc_info]


def test_ingest_reads_the_page_text_of_a_report_with_no_pdf(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    page_html = (
        "<h2>Synopsis</h2><p>The pilot over-corrected and the vessel grounded on the bank.</p>"
        "<figure><img src='x.png' alt='Figure 1'></figure>"
        "<table><tr><th>Place on board</th><td>Hull</td></tr></table>"
    )
    foxtrot = replace(
        report("test-report-foxtrot", None),
        report_type="completed-preliminary-examination",
        page_html=page_html,
    )
    golf = report("test-report-golf", None)  # no PDF and no page text
    monkeypatch.setattr(cli, "list_merchant_reports", lambda *a, **k: [foxtrot, golf])
    monkeypatch.setattr(cli, "download_pdf", lambda client, meta, dest: None)
    monkeypatch.setattr(cli, "embedder", lambda data_dir: HashEmbedder())

    assert cli.main(["ingest"]) == 0

    out = capsys.readouterr().out
    assert "test-report-foxtrot: no PDF, 1 page group from the GOV.UK page" in out
    assert "test-report-golf: no PDF link and no page text" in out
    assert "Reports read from their GOV.UK page (no PDF): 1" in out
    unsearchable = out.split("Reports with no PDF link and no page text, not searchable")[1]
    unsearchable = unsearchable.split("\n\n")[0]
    assert "(1):" in unsearchable
    assert "test-report-golf" in unsearchable
    with Store(tmp_path / "data" / "telltale.sqlite") as store:
        [chunk] = store.chunks()
    assert chunk.source == "https://www.gov.uk/maib-reports/test-report-foxtrot"
    assert chunk.passages == [
        "Synopsis The pilot over-corrected and the vessel grounded on the bank. Place on board: Hull"
    ]
    assert "Figure 1" not in " ".join(chunk.passages)


def test_ingest_run_twice_reuses_the_saved_embeddings(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    reports = [
        replace(
            report(f"test-report-{name}", None),
            page_html=f"<h2>Synopsis</h2><p>The {name} mooring line parted during berthing.</p>",
        )
        for name in ("hotel", "india")
    ]
    monkeypatch.setattr(cli, "list_merchant_reports", lambda *a, **k: reports)
    monkeypatch.setattr(cli, "download_pdf", lambda client, meta, dest: None)
    embedders: list[CountingEmbedder] = []

    def counting(data_dir):
        embedders.append(CountingEmbedder())
        return embedders[-1]

    monkeypatch.setattr(cli, "embedder", counting)

    assert cli.main(["ingest"]) == 0
    first = capsys.readouterr().out
    assert "No saved embeddings to reuse; embedding all 2 page groups." in first
    assert [len(call) for call in embedders[0].calls] == [2]

    assert cli.main(["ingest"]) == 0
    second = capsys.readouterr().out
    assert "Reused 2 saved embeddings; no new page groups to embed." in second
    assert embedders[1].calls == []
    assert "Search index saved to" in second
    with Store(tmp_path / "data" / "telltale.sqlite") as store:
        chunks = store.chunks()
    index = HybridIndex.load(tmp_path / "data" / "index", chunks, CountingEmbedder())
    assert index.search("hotel mooring line")[0].report_slug == "test-report-hotel"


# What was searched: shown on a no-answer result, and always saved with `--save`.

SEARCHED_HEADING = "Reports searched (closest first):"
SEARCHED_CLOSING = "These were searched and did not match the job. They are not an answer."


def searched_chunk(id: int, letter: str, page: int, web_page: bool = False) -> Chunk:
    url = (
        f"https://www.gov.uk/maib-reports/test-report-{letter}"
        if web_page
        else f"https://assets.publishing.service.gov.uk/media/test/report-{letter}.pdf"
    )
    return Chunk(
        id=id,
        report_slug=f"test-report-{letter}",
        report_title=f"Test report {letter}",
        url=url,
        page=page,
        passages=[f"Passage {id}."],
        web_page=web_page,
    )


# Seven chunks across six reports, in retrieval order; report a appears twice.
SEARCHED = [
    searched_chunk(1, "a", 3),
    searched_chunk(2, "b", 7),
    searched_chunk(3, "a", 9),
    searched_chunk(4, "c", 1),
    searched_chunk(5, "d", 2),
    searched_chunk(6, "e", 5),
    searched_chunk(7, "f", 8),
]


def test_ask_lists_the_reports_searched_when_there_is_no_answer(ask_offline, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "answer", stub_answer([Sentence(text=NO_ANSWER, citations=[])], chunks=SEARCHED)
    )

    assert cli.main(["ask", "opening up an auxiliary engine crankcase"]) == 0

    out = capsys.readouterr().out
    assert out.startswith(NO_ANSWER + "\n")
    listed = out.split(SEARCHED_HEADING)[1].split(SEARCHED_CLOSING)[0]
    entries = [line for line in listed.splitlines() if line.strip()]
    pdf = "https://assets.publishing.service.gov.uk/media/test/report-{}.pdf#page={}"
    assert entries == [
        f"- Test report a, page 3: {pdf.format('a', 3)}",
        f"- Test report b, page 7: {pdf.format('b', 7)}",
        f"- Test report c, page 1: {pdf.format('c', 1)}",
        f"- Test report d, page 2: {pdf.format('d', 2)}",
        f"- Test report e, page 5: {pdf.format('e', 5)}",
    ]
    assert out.index(NO_ANSWER) < out.index(SEARCHED_HEADING) < out.index(SEARCHED_CLOSING)
    assert SEARCHED_CLOSING + "\n" in out
    assert "Test report f" not in out
    assert "—" not in out


def test_ask_lists_no_reports_searched_for_a_sourced_answer(ask_offline, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "answer", stub_answer([Sentence(text=SOURCED, citations=[CITATION])], chunks=SEARCHED)
    )

    cli.main(["ask", "entering a ballast tank"])

    out = capsys.readouterr().out
    assert SEARCHED_HEADING not in out
    assert SEARCHED_CLOSING not in out


@pytest.mark.parametrize(
    "sentences",
    [[Sentence(text=SOURCED, citations=[CITATION])], [Sentence(text=NO_ANSWER, citations=[])]],
    ids=["sourced", "no-answer"],
)
def test_ask_save_records_every_chunk_searched(
    ask_offline, monkeypatch, tmp_path: Path, capsys, sentences
):
    import json

    chunks = [*SEARCHED, searched_chunk(8, "g", 1, web_page=True)]
    monkeypatch.setattr(cli, "answer", stub_answer(sentences, chunks=chunks))
    saved = tmp_path / "answer.json"

    assert cli.main(["ask", "a question", "--save", str(saved)]) == 0

    data = json.loads(saved.read_text(encoding="utf-8"))
    pdf = "https://assets.publishing.service.gov.uk/media/test/report-{}.pdf#page={}"
    assert data["searched"] == [
        {"report_title": "Test report a", "page": 3, "source": pdf.format("a", 3),
         "report_slug": "test-report-a", "source_id": "maib"},
        {"report_title": "Test report b", "page": 7, "source": pdf.format("b", 7),
         "report_slug": "test-report-b", "source_id": "maib"},
        {"report_title": "Test report a", "page": 9, "source": pdf.format("a", 9),
         "report_slug": "test-report-a", "source_id": "maib"},
        {"report_title": "Test report c", "page": 1, "source": pdf.format("c", 1),
         "report_slug": "test-report-c", "source_id": "maib"},
        {"report_title": "Test report d", "page": 2, "source": pdf.format("d", 2),
         "report_slug": "test-report-d", "source_id": "maib"},
        {"report_title": "Test report e", "page": 5, "source": pdf.format("e", 5),
         "report_slug": "test-report-e", "source_id": "maib"},
        {"report_title": "Test report f", "page": 8, "source": pdf.format("f", 8),
         "report_slug": "test-report-f", "source_id": "maib"},
        # A GOV.UK page has no page numbers, so none is recorded.
        {
            "report_title": "Test report g",
            "page": None,
            "source": "https://www.gov.uk/maib-reports/test-report-g",
            "report_slug": "test-report-g",
            "source_id": "maib",
        },
    ]


# --- telltale eval --------------------------------------------------------------------------


def _eval_questions(path: Path) -> None:
    """A synthetic question file: one working question and one blind one."""
    rows = [
        {"id": "W1", "question": "Synthetic working question?", "expect": "reports",
         "expected_reports": ["synthetic-alpha"], "related_reports": [], "blind": False},
        {"id": "B1", "question": "Synthetic blind question?", "expect": "reports",
         "expected_reports": ["synthetic-alpha"], "related_reports": [], "blind": True},
    ]
    path.write_text(json.dumps({"version": 1, "questions": rows}), encoding="utf-8")


@pytest.fixture
def eval_offline(monkeypatch, tmp_path: Path):
    """`telltale eval` with a synthetic question file and store, a clean tree, and no index,
    client or model: each of those records a call instead. Returns the call list."""
    calls: list[str] = []
    questions = tmp_path / "questions.json"
    _eval_questions(questions)
    with Store(tmp_path / cli.STORE_FILE) as store:
        store.upsert_report(report("synthetic-alpha", PDF_URL))
    settings = Settings(anthropic_api_key="test-key", data_dir=tmp_path)
    monkeypatch.setattr(cli, "QUESTIONS_FILE", questions)
    monkeypatch.setattr(cli, "load_settings", lambda *a, **k: settings)
    monkeypatch.setattr(cli, "_git_state", lambda: ("abc1234", False))

    def fake_index(data_dir):
        calls.append("load_index")
        return object()

    def fake_client(**kwargs):
        calls.append("client")
        return object()

    def fake_answer(question, index, client, model):
        calls.append(f"answer:{question}")
        return stub_answer([Sentence(text=SOURCED, citations=[CITATION])])(
            question, index, client, model
        )

    def fake_judge(saved, client, model):
        calls.append(f"judge:{model}")
        return {}, [], 0.0

    monkeypatch.setattr(cli, "load_index", fake_index)
    monkeypatch.setattr(cli.anthropic, "Anthropic", fake_client)
    monkeypatch.setattr(cli, "answer", fake_answer)
    monkeypatch.setattr(cli, "judge_answer", fake_judge)
    return calls


def test_eval_without_final_refuses_a_blind_id_before_any_model_call(eval_offline, capsys):
    assert cli.main(["eval", "--only", "B1"]) == 1
    assert "B1 is a blind question" in capsys.readouterr().err
    assert eval_offline == []


def test_eval_final_refuses_a_dirty_tree_before_any_model_call(
    eval_offline, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "_git_state", lambda: ("abc1234", True))
    assert cli.main(["eval", "--final"]) == 1
    assert "uncommitted" in capsys.readouterr().err
    assert eval_offline == []


def test_eval_prints_the_run_directory(eval_offline, tmp_path: Path, capsys):
    assert cli.main(["eval", "--judge-model", "claude-haiku-5-5"]) == 0

    out = capsys.readouterr().out
    runs = list((tmp_path / "eval" / "runs").iterdir())
    assert len(runs) == 1
    assert str(runs[0]) in out
    # Only the working question is asked, with the judge model named on the command line.
    assert eval_offline == [
        "load_index", "client", "answer:Synthetic working question?", "judge:claude-haiku-5-5"
    ]
    meta = json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))
    assert meta["question_ids"] == ["W1"]
    assert meta["commit"] == "abc1234"
    assert not (tmp_path / "eval" / "final-runs.jsonl").exists()


def test_eval_final_runs_the_blind_set_and_logs_it(eval_offline, tmp_path: Path):
    assert cli.main(["eval", "--final"]) == 0

    assert "answer:Synthetic blind question?" in eval_offline
    assert "answer:Synthetic working question?" not in eval_offline
    lines = (tmp_path / "eval" / "final-runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["commit"] == "abc1234"


def test_eval_with_no_question_file_says_where_it_should_be(
    eval_offline, monkeypatch, tmp_path: Path, capsys
):
    missing = tmp_path / "private" / "questions.json"
    monkeypatch.setattr(cli, "QUESTIONS_FILE", missing)

    assert cli.main(["eval"]) == 1
    assert str(missing) in capsys.readouterr().err
    assert eval_offline == []


def test_eval_refuses_an_unknown_id(eval_offline, capsys):
    assert cli.main(["eval", "--only", "W1,Z9"]) == 1
    assert "Z9" in capsys.readouterr().err
    assert eval_offline == []


def test_eval_default_cost_cap_is_one_dollar():
    args = cli._parser().parse_args(["eval"])
    assert args.max_cost == 1.00
    assert args.only is None


@pytest.mark.parametrize("flag", ["--model", "--judge-model"])
def test_eval_refuses_a_model_with_no_price_before_any_call(
    eval_offline, monkeypatch, flag, capsys
):
    def no_key(command):
        eval_offline.append("key")
        raise AssertionError("the key must not be read")

    monkeypatch.setattr(cli, "_settings_with_key", no_key)
    assert cli.main(["eval", flag, "claude-unpriced-test"]) == 1
    err = capsys.readouterr().err
    assert "claude-unpriced-test" in err
    assert "price" in err
    assert eval_offline == []


def test_eval_refuses_a_judge_model_with_no_thinking_off_setting_before_any_call(
    eval_offline, monkeypatch, capsys
):
    from telltale.pricing import PRICES

    monkeypatch.setitem(PRICES, "claude-unlisted-test", (1.0, 1.0))
    monkeypatch.setattr(cli, "_settings_with_key", lambda command: eval_offline.append("key"))
    assert cli.main(["eval", "--judge-model", "claude-unlisted-test"]) == 1
    err = capsys.readouterr().err
    assert "claude-unlisted-test" in err
    assert "thinking" in err
    assert eval_offline == []


def test_eval_refuses_an_answer_model_with_no_thinking_setting_before_any_call(
    eval_offline, monkeypatch, capsys
):
    from telltale.pricing import PRICES

    # Priced, so only the missing thinking-off setting is in the way.
    monkeypatch.setitem(PRICES, "claude-unlisted-test", (1.0, 1.0))
    monkeypatch.setattr(cli, "_settings_with_key", lambda command: eval_offline.append("key"))
    assert cli.main(["eval", "--model", "claude-unlisted-test"]) == 1
    err = capsys.readouterr().err
    assert "claude-unlisted-test" in err
    assert "THINKING_OFF in telltale/thinking.py" in err
    assert eval_offline == []


def test_eval_passes_the_chosen_models_to_answer_and_judge(eval_offline, monkeypatch, tmp_path):
    seen: list[tuple[str, str]] = []
    real_answer = cli.answer

    def spy_answer(question, index, client, model):
        seen.append(("answer", model))
        return real_answer(question, index, client, model)

    monkeypatch.setattr(cli, "answer", spy_answer)
    assert cli.main(
        ["eval", "--model", "claude-sonnet-5-5", "--judge-model", "claude-haiku-5-5"]
    ) == 0
    assert seen == [("answer", "claude-sonnet-5-5")]
    assert "judge:claude-haiku-5-5" in eval_offline
    (run_dir,) = (tmp_path / "eval" / "runs").iterdir()
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert (meta["model"], meta["judge_model"]) == ("claude-sonnet-5-5", "claude-haiku-5-5")


def test_eval_interrupted_says_where_the_run_is(eval_offline, monkeypatch, tmp_path, capsys):
    def interrupted(question, index, client, model):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "answer", interrupted)
    assert cli.main(["eval", "--final"]) == 130

    captured = capsys.readouterr()
    (run_dir,) = (tmp_path / "eval" / "runs").iterdir()
    assert str(run_dir) in captured.out
    assert "nothing is downloaded twice" not in captured.err
    assert "Stopped" in captured.err
    lines = (tmp_path / "eval" / "final-runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_eval_batch_hands_the_index_and_client_to_a_batch_run(
    eval_offline, monkeypatch, tmp_path: Path, capsys
):
    seen: dict = {}
    real_run_eval = cli.run_eval
    index = SimpleNamespace(search=lambda query, k: [])

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_run_eval(*args, **kwargs)

    monkeypatch.setattr(cli, "run_eval", spy)
    monkeypatch.setattr(cli, "load_index", lambda data_dir: index)
    assert cli.main(["eval", "--batch"]) == 0

    assert seen["batch"] is True
    assert seen["index"] is index
    assert seen["client"] is not None
    # Batch mode never asks or judges one question at a time.
    assert not [call for call in eval_offline if call.startswith(("answer:", "judge:"))]
    out = capsys.readouterr().out
    assert "Batch mode" in out
    (run_dir,) = (tmp_path / "eval" / "runs").iterdir()
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    # The one question found nothing, so no batch was needed.
    assert (meta["answer_batch_id"], meta["judge_batch_id"]) == (None, None)
    assert (run_dir / "report.md").is_file()


def test_eval_without_batch_runs_live(eval_offline, monkeypatch):
    seen: dict = {}
    real_run_eval = cli.run_eval

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_run_eval(*args, **kwargs)

    monkeypatch.setattr(cli, "run_eval", spy)
    assert cli.main(["eval"]) == 0
    assert seen["batch"] is False
    assert "answer:Synthetic working question?" in eval_offline


def test_eval_batch_interrupted_says_the_batch_keeps_running(
    eval_offline, monkeypatch, tmp_path, capsys
):
    def interrupted(*args, **kwargs):
        kwargs["on_start"](tmp_path)
        kwargs["on_batch"]("answer", "msgbatch_test_1")
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_eval", interrupted)
    assert cli.main(["eval", "--batch"]) == 130
    err = capsys.readouterr().err
    assert "Stopped" in err
    assert "keeps running and is still charged" in err


def test_eval_interrupted_before_the_run_starts_says_nothing_was_asked(
    eval_offline, monkeypatch, capsys
):
    def interrupted(data_dir):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "load_index", interrupted)
    assert cli.main(["eval", "--final", "--batch"]) == 130
    err = capsys.readouterr().err
    assert "nothing was asked" in err
    assert "log" not in err
    assert "batch" not in err


def test_eval_interrupted_working_run_does_not_claim_a_final_log(
    eval_offline, monkeypatch, capsys
):
    def interrupted(question, index, client, model):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "answer", interrupted)
    assert cli.main(["eval"]) == 130
    err = capsys.readouterr().err
    assert "saved in the run folder" in err
    assert "log" not in err
    # No batch was sent, so none is said to keep running.
    assert "keeps running" not in err


def test_eval_interrupted_final_run_says_it_is_in_the_log(eval_offline, monkeypatch, capsys):
    def interrupted(question, index, client, model):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "answer", interrupted)
    assert cli.main(["eval", "--final"]) == 130
    assert "already in the final-run log" in capsys.readouterr().err


def test_eval_prints_the_unjudged_count_after_the_cost(eval_offline, capsys):
    # The fake judge gives no verdict, so the one cited sentence is unjudged.
    assert cli.main(["eval"]) == 0
    out = capsys.readouterr().out
    assert "Unjudged: 1 of 1 cited sentences" in out
    assert out.index("Cost: $") < out.index("Unjudged:")


def test_eval_stops_with_one_line_when_the_api_refuses_a_request(
    eval_offline, monkeypatch, tmp_path, capsys
):
    import anthropic
    import httpx2

    def refused(question, index, client, model):
        response = httpx2.Response(
            400, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        )
        raise anthropic.BadRequestError("Synthetic bad request.", response=response, body=None)

    monkeypatch.setattr(cli, "answer", refused)
    assert cli.main(["eval"]) == 1
    err = capsys.readouterr().err
    (run_dir,) = (tmp_path / "eval" / "runs").iterdir()
    [line] = err.strip().splitlines()
    assert "BadRequestError" in line
    assert str(run_dir) in line


def test_eval_where_every_asked_question_failed_exits_non_zero(eval_offline, monkeypatch, capsys):
    def broken(question, index, client, model):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(cli, "answer", broken)
    assert cli.main(["eval"]) == 1
    captured = capsys.readouterr()
    assert "W1 failed: RuntimeError: synthetic failure" in captured.out
    assert "Every question asked failed" in captured.err


# --- telltale eval-report -------------------------------------------------------------------


def test_eval_writes_the_report_and_prints_its_path(eval_offline, tmp_path: Path, capsys):
    assert cli.main(["eval"]) == 0

    out = capsys.readouterr().out
    (run_dir,) = (tmp_path / "eval" / "runs").iterdir()
    assert (run_dir / "report.md").is_file()
    assert (run_dir / "judge-review.csv").is_file()
    assert str(run_dir / "report.md") in out


def test_eval_writes_the_report_after_an_early_stop(eval_offline, monkeypatch, tmp_path: Path):
    two = tmp_path / "two.json"
    rows = [
        {"id": f"W{n}", "question": f"Synthetic working question {n}?", "expect": "reports",
         "expected_reports": ["synthetic-alpha"], "related_reports": [], "blind": False}
        for n in (1, 2)
    ]
    two.write_text(json.dumps({"version": 1, "questions": rows}), encoding="utf-8")
    monkeypatch.setattr(cli, "QUESTIONS_FILE", two)

    # The first answer costs more than the cap, so the second question is never asked.
    assert cli.main(["eval", "--max-cost", "0.001"]) == 0

    (run_dir,) = (tmp_path / "eval" / "runs").iterdir()
    text = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "Stopped early: yes" in text
    assert "| not asked |" in text


def test_eval_report_rebuilds_a_run_without_the_key_or_a_model(
    eval_offline, monkeypatch, tmp_path: Path, capsys
):
    assert cli.main(["eval"]) == 0
    (run_dir,) = (tmp_path / "eval" / "runs").iterdir()
    before = (run_dir / "report.md").read_text(encoding="utf-8")
    (run_dir / "report.md").unlink()
    capsys.readouterr()
    eval_offline.clear()

    def no_key(command):
        raise AssertionError("eval-report must not read the key")

    monkeypatch.setattr(cli, "_settings_with_key", no_key)
    assert cli.main(["eval-report", str(run_dir)]) == 0

    assert eval_offline == []
    assert (run_dir / "report.md").read_text(encoding="utf-8") == before
    out = capsys.readouterr().out
    assert str(run_dir / "report.md") in out
    assert str(run_dir / "judge-review.csv") in out


def test_eval_report_after_a_final_run_shows_no_blind_question_text(
    eval_offline, tmp_path: Path
):
    assert cli.main(["eval", "--final"]) == 0
    (run_dir,) = (tmp_path / "eval" / "runs").iterdir()

    assert cli.main(["eval-report", str(run_dir)]) == 0

    for name, encoding in (("report.md", "utf-8"), ("judge-review.csv", "utf-8-sig")):
        assert "Synthetic blind question?" not in (run_dir / name).read_text(encoding=encoding)
    assert "B1" in (run_dir / "report.md").read_text(encoding="utf-8")


def test_eval_report_on_a_folder_with_no_run_says_so(eval_offline, tmp_path: Path, capsys):
    empty = tmp_path / "not-a-run"
    empty.mkdir()

    assert cli.main(["eval-report", str(empty)]) == 1
    assert "run.json" in capsys.readouterr().err


def test_eval_rejudge_judges_the_saved_answers_again_and_writes_a_report(
    eval_offline, monkeypatch, tmp_path: Path, capsys
):
    assert cli.main(["eval"]) == 0
    (source,) = (tmp_path / "eval" / "runs").iterdir()
    eval_offline.clear()
    capsys.readouterr()
    seen: dict = {}

    def fake_rejudge(source_dir, questions, out_dir, client, max_cost, **kw):
        seen.update(source=source_dir, ids=[q.id for q in questions], out=out_dir,
                    max_cost=max_cost, **kw)
        return source_dir  # a finished run folder, so the report can be written

    monkeypatch.setattr(cli, "rejudge_run", fake_rejudge)
    (source / "report.md").unlink()
    assert cli.main(["eval-rejudge", str(source)]) == 0

    assert seen["source"] == source
    assert "B1" not in seen["ids"]  # working questions only
    assert seen["out"] == tmp_path / "eval" / "runs"
    assert seen["max_cost"] == 1.00
    assert seen["judge_model"] == cli.DEFAULT_JUDGE_MODEL
    assert seen["commit"] == "abc1234"
    # No answer is asked again.
    assert not [call for call in eval_offline if call.startswith("answer:")]
    assert (source / "report.md").is_file()
    assert "Re-judging" in capsys.readouterr().out


def test_eval_rejudge_on_a_folder_with_no_run_says_so_before_the_key(
    eval_offline, monkeypatch, tmp_path: Path, capsys
):
    empty = tmp_path / "not-a-run"
    empty.mkdir()

    def no_key(command):
        raise AssertionError("must refuse before reading the key")

    monkeypatch.setattr(cli, "_settings_with_key", no_key)
    assert cli.main(["eval-rejudge", str(empty)]) == 1
    assert "run.json" in capsys.readouterr().err
    assert eval_offline == []


def test_eval_rejudge_refuses_a_final_run(eval_offline, tmp_path: Path, capsys):
    assert cli.main(["eval", "--final"]) == 0
    (source,) = (tmp_path / "eval" / "runs").iterdir()
    capsys.readouterr()

    assert cli.main(["eval-rejudge", str(source)]) == 1
    assert "final run" in capsys.readouterr().err


def test_eval_rejudge_on_an_unreadable_run_file_says_so(eval_offline, tmp_path: Path, capsys):
    broken = tmp_path / "broken-run"
    broken.mkdir()
    (broken / "run.json").write_text("{not json", encoding="utf-8")

    assert cli.main(["eval-rejudge", str(broken)]) == 1
    assert "run.json" in capsys.readouterr().err


def test_eval_rejudge_refuses_a_run_file_with_no_model_before_the_key(
    eval_offline, monkeypatch, tmp_path: Path, capsys
):
    odd = tmp_path / "odd-run"
    odd.mkdir()
    (odd / "run.json").write_text('{"final": false}', encoding="utf-8")

    def no_key(command):
        raise AssertionError("must refuse before reading the key")

    monkeypatch.setattr(cli, "_settings_with_key", no_key)
    assert cli.main(["eval-rejudge", str(odd)]) == 1
    assert "not a run file" in capsys.readouterr().err


def test_eval_rejudge_on_a_run_file_that_is_not_utf8_says_so(eval_offline, tmp_path, capsys):
    odd = tmp_path / "binary-run"
    odd.mkdir()
    (odd / "run.json").write_bytes(b"\xff\xfe\x00garbage")

    assert cli.main(["eval-rejudge", str(odd)]) == 1
    assert "run.json" in capsys.readouterr().err



def test_eval_public_set_writes_only_working_questions(
    eval_offline, monkeypatch, tmp_path: Path, capsys
):
    def no_key(command):
        raise AssertionError("eval-public-set must not read the key")

    monkeypatch.setattr(cli, "_settings_with_key", no_key)
    out = tmp_path / "site" / "test-set.md"
    assert cli.main(["eval-public-set", "--out", str(out)]) == 0

    page = out.read_text(encoding="utf-8")
    assert "Synthetic working question?" in page
    assert "W1" in page
    assert "Synthetic blind question?" not in page
    assert "B1" not in page
    assert "[Test report synthetic-alpha](<https://www.gov.uk/maib-reports/synthetic-alpha>)" in page
    assert "1 working question" in page
    assert str(out) in capsys.readouterr().out
    assert eval_offline == []


def test_eval_public_set_writes_to_docs_test_set_by_default():
    args = cli._parser().parse_args(["eval-public-set"])
    assert args.out == Path("docs/test-set.md")


def test_cli_sources_show_each_publisher_credit(ask_offline, monkeypatch, capsys):
    atsb = Citation(
        report_title="Test report bravo",
        source="https://www.atsb.gov.au/test/report-bravo.pdf#page=9",
        cited_text="The second officer collapsed.",
        source_id="atsb",
    )
    monkeypatch.setattr(cli, "answer", stub_answer([
        Sentence(text=SOURCED, citations=[CITATION]),
        Sentence(text="The second officer collapsed at the foot of the ladder.", citations=[atsb]),
    ]))

    assert cli.main(["ask", "entering a ballast tank"]) == 0

    sources = capsys.readouterr().out.split("Sources:")[1].split("Cost:")[0]
    entries = [line for line in sources.splitlines() if line.startswith("[")]
    assert entries == [
        "[1] Test report alpha, page 4. Crown copyright.",
        "[2] Test report bravo, page 9. CC BY 4.0, ATSB.",
    ]


# `telltale ingest --source`: which publishers are listed, and the counts per publisher.


def publisher_report(slug: str, source: str, pdf: bool = True) -> ReportMeta:
    return replace(
        report(slug, f"https://reports.example.test/{slug}.pdf" if pdf else None),
        page_url=f"https://reports.example.test/{slug}",
        report_type="Report",
        source=source,
    )


@pytest.fixture
def ingest_offline(monkeypatch, tmp_path: Path):
    """Fake listers for MAIB, NTSB and TSIB and a fake download, recording each call in order.
    Each lister returns one report, and passes one dropped and one out-of-scope result to
    `on_skip`. No request is made and no OCR or embedding model is loaded."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    events: list[tuple] = []

    def lister(source: str):
        def fake(client, *, limit=None, on_skip=None, export_path=None):
            events.append(("list", source, limit, export_path))
            on_skip(SkippedReport(
                f"{source}-unread", f"Unread {source} result", f"https://reports.example.test/{source}-unread",
                "page request failed", dropped=True,
            ))
            on_skip(SkippedReport(f"{source}-out", "Out", "", "fishing vessel", dropped=False))
            return [publisher_report(f"{source}-report-alpha", source)]
        return fake

    def fake_download(client, meta, dest):
        events.append(("download", meta.slug))
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"{meta.slug}.pdf"
        write_text_pdf(path, f"The {meta.source} mooring line parted.")
        return path

    monkeypatch.setattr(cli, "list_merchant_reports", lister("maib"))
    monkeypatch.setattr(cli, "list_ntsb_reports", lister("ntsb"))
    monkeypatch.setattr(cli, "list_tsib_reports", lister("tsib"))
    monkeypatch.setattr(cli, "download_pdf", fake_download)
    monkeypatch.setattr(cli, "embedder", lambda data_dir: HashEmbedder())
    export = tmp_path / "carol.json"
    export.write_text('{"results": []}')
    return SimpleNamespace(events=events, export=export, data=tmp_path / "data")


def listed(events: list[tuple]) -> list[str]:
    return [event[1] for event in events if event[0] == "list"]


@pytest.mark.parametrize("source", ["tsib", "ntsb"])
def test_ingest_source_lists_only_that_publisher(ingest_offline, source):
    args = ["ingest", "--source", source]
    if source == "ntsb":
        args += ["--ntsb-export", str(ingest_offline.export)]

    assert cli.main(args) == 0

    assert listed(ingest_offline.events) == [source]
    if source == "ntsb":
        [event] = [e for e in ingest_offline.events if e[0] == "list"]
        assert event[3] == ingest_offline.export
    with Store(ingest_offline.data / "telltale.sqlite") as store:
        assert store.sources() == [source]
        chunks = store.chunks()
    assert [c.source_id for c in chunks] == [source]
    index = HybridIndex.load(ingest_offline.data / "index", chunks, HashEmbedder())
    assert index.search("mooring line parted")


def test_ingest_all_lists_each_publisher_in_turn(ingest_offline, capsys):
    assert cli.main(
        ["ingest", "--source", "all", "--ntsb-export", str(ingest_offline.export), "--limit", "5"]
    ) == 0

    # Each publisher is listed and its reports stored before the next is listed.
    assert ingest_offline.events == [
        ("list", "maib", 5, None),
        ("download", "maib-report-alpha"),
        ("list", "ntsb", 5, ingest_offline.export),
        ("download", "ntsb-report-alpha"),
        ("list", "tsib", 5, None),
        ("download", "tsib-report-alpha"),
    ]
    out = capsys.readouterr().out
    # The index is built once, at the end, over the whole store.
    assert out.count("Building the search index") == 1
    assert out.index("Building the search index") > out.index("tsib-report-alpha")
    with Store(ingest_offline.data / "telltale.sqlite") as store:
        chunks = store.chunks()
    index = HybridIndex.load(ingest_offline.data / "index", chunks, HashEmbedder())
    assert {c.source_id for c in index.search("mooring line parted", k=10)} == {
        "maib", "ntsb", "tsib"
    }


def test_ingest_default_is_maib(ingest_offline, capsys):
    assert cli.main(["ingest"]) == 0

    assert ingest_offline.events == [
        ("list", "maib", None, None),
        ("download", "maib-report-alpha"),
    ]
    assert "Listing MAIB reports (one request per report; this takes a while)." in (
        capsys.readouterr().out
    )


def test_ingest_prints_counts_per_publisher(ingest_offline, monkeypatch, capsys):
    with Store(ingest_offline.data / "telltale.sqlite") as store:
        # From an earlier run: a MAIB report read from its GOV.UK page.
        store.upsert_report(report("maib-report-page", None))
        store.replace_chunks("maib-report-page", [(1, ["Read from the page.", "Twice."])])

    assert cli.main(["ingest", "--source", "all", "--ntsb-export", str(ingest_offline.export)]) == 0

    out = capsys.readouterr().out
    store_counts = out.split("all runs so far:\n")[1].split("Search index saved")[0]
    assert store_counts == (
        "  MAIB (Marine Accident Investigation Branch):\n"
        "    Reports:                 2\n"
        "      Report: 1\n"
        "      investigation-report: 1\n"
        "    Reports with a PDF link: 1\n"
        "    Reports read from their GOV.UK page (no PDF): 1\n"
        "    Page groups (chunks):    2\n"
        "    Passages:                3\n"
        "    Pages read by OCR:       0\n"
        "  NTSB (National Transportation Safety Board):\n"
        "    Reports:                 1\n"
        "      Report: 1\n"
        "    Reports with a PDF link: 1\n"
        "    Reports read from their web page (no PDF): 0\n"
        "    Page groups (chunks):    1\n"
        "    Passages:                1\n"
        "    Pages read by OCR:       0\n"
        "  TSIB (Transport Safety Investigation Bureau, Singapore):\n"
        "    Reports:                 1\n"
        "      Report: 1\n"
        "    Reports with a PDF link: 1\n"
        "    Reports read from their web page (no PDF): 0\n"
        "    Page groups (chunks):    1\n"
        "    Passages:                1\n"
        "    Pages read by OCR:       0\n"
        "  All publishers:\n"
        "    Reports:                 4\n"
        "    Reports with a PDF link: 3\n"
        "    Reports read from their web page (no PDF): 1\n"
        "    Page groups (chunks):    4\n"
        "    Passages:                5\n"
        "    Pages read by OCR:       0\n"
    )
    # Results that could not be read are listed under one heading, publisher by publisher.
    dropped = out.split("Left out of the listing because they could not be read (3):\n")[1]
    dropped = dropped.split("\n\n")[0]
    assert dropped.splitlines() == [
        "  MAIB (1):",
        "    - Unread maib result: page request failed",
        "      https://reports.example.test/maib-unread",
        "  NTSB (1):",
        "    - Unread ntsb result: page request failed",
        "      https://reports.example.test/ntsb-unread",
        "  TSIB (1):",
        "    - Unread tsib result: page request failed",
        "      https://reports.example.test/tsib-unread",
    ]
    assert "Out of scope, left out:\n  MAIB: 1 (fishing vessel 1)\n  NTSB: 1" in out


@pytest.fixture
def no_request(monkeypatch):
    """Fails the test if ingest opens an HTTP client or lists anything."""
    def refuse(*args, **kwargs):
        raise AssertionError("a request was about to be made")

    monkeypatch.setattr(cli.httpx, "Client", refuse)
    for name in ("list_merchant_reports", "list_ntsb_reports", "list_tsib_reports"):
        monkeypatch.setattr(cli, name, refuse)


def test_ingest_atsb_is_refused_before_any_request(no_request, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)

    assert cli.main(["ingest", "--source", "atsb"]) == 1

    captured = capsys.readouterr()
    assert "ATSB reports are not available yet" in captured.err
    assert "Traceback" not in captured.out + captured.err
    assert not (tmp_path / "data").exists()


def test_ingest_ntsb_without_export_is_refused(no_request, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)

    assert cli.main(["ingest", "--source", "ntsb"]) == 1

    err = capsys.readouterr().err
    assert cli.NTSB_NEEDS_EXPORT in err
    assert "https://my.ntsb.gov/basic" in err
    assert "--ntsb-export PATH" in err
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize(
    "args, message",
    [
        (["--source", "ntsb", "--ntsb-export", "missing.json"], "No CAROL export at missing.json"),
        (["--source", "all", "--ntsb-export", "missing.json"], "No CAROL export at missing.json"),
        (["--ntsb-export", "carol.json"], "--ntsb-export is used only with --source ntsb or all"),
    ],
)
def test_ingest_refuses_a_misused_ntsb_export_before_any_request(
    no_request, monkeypatch, tmp_path, capsys, args, message
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "carol.json").write_text("{}")

    assert cli.main(["ingest", *args]) == 1

    assert message in capsys.readouterr().err


def test_ingest_all_without_ntsb_export_skips_ntsb_and_carries_on(ingest_offline, capsys):
    assert cli.main(["ingest", "--source", "all"]) == 0

    assert listed(ingest_offline.events) == ["maib", "tsib"]
    out = capsys.readouterr().out
    assert f"Skipping NTSB. {cli.NTSB_NEEDS_EXPORT}" in out
    assert out.index("Skipping NTSB") < out.index("Listing MAIB reports")
    with Store(ingest_offline.data / "telltale.sqlite") as store:
        assert store.sources() == ["maib", "tsib"]


def test_ingest_all_carries_on_past_a_failed_listing(ingest_offline, monkeypatch, capsys):
    def failing_list(client, *, limit=None, on_skip=None):
        ingest_offline.events.append(("list", "tsib", limit, None))
        request = httpx.Request("GET", "https://www.mot.gov.sg/")
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(cli, "list_tsib_reports", failing_list)

    assert cli.main(["ingest", "--source", "all", "--ntsb-export", str(ingest_offline.export)]) == 1

    assert listed(ingest_offline.events) == ["maib", "ntsb", "tsib"]
    captured = capsys.readouterr()
    assert "Search index saved" in captured.out
    assert "Could not list TSIB reports: ConnectError" in captured.err
    with Store(ingest_offline.data / "telltale.sqlite") as store:
        assert store.sources() == ["maib", "ntsb"]


def test_serve_footer_names_every_publisher_in_the_store(no_uvicorn, monkeypatch, tmp_path: Path):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "embedder", lambda data_dir: HashEmbedder())
    with Store(tmp_path / "data" / "telltale.sqlite") as store:
        store.upsert_report(report("test-report-alpha", PDF_URL))
        store.upsert_report(publisher_report("ntsb-report-bravo", "ntsb"))
        store.replace_chunks("ntsb-report-bravo", [(4, ["The bosun entered the tank."])])
        HybridIndex.build(store.chunks(), HashEmbedder()).save(tmp_path / "data" / "index")

    assert cli.main(["serve"]) == 0

    [call] = no_uvicorn
    page = TestClient(call["app"]).get("/").text
    assert "Reports: UK MAIB, Crown copyright; US NTSB, US Government work." in page


def test_ingest_pauses_before_each_ntsb_and_tsib_download(ingest_offline, monkeypatch):
    monkeypatch.setattr(cli, "_sleep", lambda seconds: ingest_offline.events.append(("sleep", seconds)))

    assert cli.main(
        ["ingest", "--source", "all", "--ntsb-export", str(ingest_offline.export)]
    ) == 0

    assert [e for e in ingest_offline.events if e[0] in ("sleep", "download")] == [
        ("download", "maib-report-alpha"),
        ("sleep", 1.5),
        ("download", "ntsb-report-alpha"),
        ("sleep", 1.0),
        ("download", "tsib-report-alpha"),
    ]


def test_ingest_does_not_pause_for_a_pdf_already_downloaded(ingest_offline, monkeypatch):
    from telltale.ingest.download import pdf_path

    monkeypatch.setattr(cli, "_sleep", lambda seconds: ingest_offline.events.append(("sleep", seconds)))
    kept = pdf_path(publisher_report("tsib-report-alpha", "tsib"), ingest_offline.data / "pdf")
    kept.parent.mkdir(parents=True)
    write_text_pdf(kept, "Kept from an earlier run.")

    assert cli.main(["ingest", "--source", "tsib"]) == 0

    assert ("sleep", 1.0) not in ingest_offline.events


def test_ingest_all_does_not_count_a_failed_listing_as_out_of_scope(
    ingest_offline, monkeypatch, capsys
):
    def failing_list(client, *, limit=None, on_skip=None):
        request = httpx.Request("GET", "https://www.mot.gov.sg/")
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(cli, "list_tsib_reports", failing_list)

    assert cli.main(["ingest", "--source", "all", "--ntsb-export", str(ingest_offline.export)]) == 1

    out = capsys.readouterr().out
    left_out = out.split("Out of scope, left out:\n")[1].split("\n\n")[0]
    assert left_out.splitlines() == [
        "  MAIB: 1 (fishing vessel 1)",
        "  NTSB: 1 (fishing vessel 1)",
        "  TSIB: listing failed",
    ]


def test_ingest_all_prints_what_was_left_out_before_every_listing_fails(
    ingest_offline, monkeypatch, capsys
):
    def failing(source):
        def fake(client, *, limit=None, on_skip=None, export_path=None):
            on_skip(SkippedReport(
                f"{source}-unread", f"Unread {source} result",
                f"https://reports.example.test/{source}-unread", "page request failed",
                dropped=True,
            ))
            on_skip(SkippedReport(f"{source}-out", "Out", "", "fishing vessel", dropped=False))
            raise cli.ListingError(f"{source} pages in a row could not be read")
        return fake

    for name, source in (
        ("list_merchant_reports", "maib"), ("list_ntsb_reports", "ntsb"),
        ("list_tsib_reports", "tsib"),
    ):
        monkeypatch.setattr(cli, name, failing(source))

    assert cli.main(["ingest", "--source", "all", "--ntsb-export", str(ingest_offline.export)]) == 1

    captured = capsys.readouterr()
    assert "Could not list TSIB reports: tsib pages in a row" in captured.err
    dropped = captured.out.split(
        "Left out of the listing because they could not be read (3):\n"
    )[1].split("\n\n")[0]
    assert "https://reports.example.test/ntsb-unread" in dropped
    assert (
        "Out of scope, left out:\n  MAIB: 1 (fishing vessel 1) before the listing failed"
    ) in captured.out
    assert "Building the search index" not in captured.out


def test_ingest_of_one_publisher_gives_no_all_publishers_total(ingest_offline, capsys):
    assert cli.main(["ingest", "--source", "tsib"]) == 0

    out = capsys.readouterr().out
    assert "  TSIB (Transport Safety Investigation Bureau, Singapore):" in out
    assert "All publishers" not in out


def test_ask_refuses_a_model_with_no_thinking_off_setting_before_the_index(
    ask_offline, monkeypatch, tmp_path: Path, capsys
):
    settings = Settings(anthropic_api_key="test-key", data_dir=tmp_path, model="claude-unlisted-test")
    monkeypatch.setattr(cli, "load_settings", lambda *a, **k: settings)

    def no_index(data_dir):
        raise AssertionError("the index must not be loaded")

    monkeypatch.setattr(cli, "load_index", no_index)

    assert cli.main(["ask", "entering a ballast tank"]) == 1

    err = capsys.readouterr().err
    assert "claude-unlisted-test" in err
    assert "THINKING_OFF in telltale/thinking.py" in err


def test_serve_refuses_a_model_with_no_thinking_off_setting_before_the_index(
    no_uvicorn, monkeypatch, tmp_path: Path, capsys
):
    settings = Settings(anthropic_api_key="test-key", data_dir=tmp_path, model="claude-unlisted-test")
    monkeypatch.setattr(cli, "load_settings", lambda *a, **k: settings)

    def no_index(data_dir):
        raise AssertionError("the index must not be loaded")

    monkeypatch.setattr(cli, "load_index", no_index)

    assert cli.main(["serve"]) == 1

    err = capsys.readouterr().err
    assert "claude-unlisted-test" in err
    assert "THINKING_OFF in telltale/thinking.py" in err
    assert no_uvicorn == []


def test_eval_public_set_without_a_store_says_so_and_creates_none(
    eval_offline, tmp_path: Path, capsys
):
    (tmp_path / cli.STORE_FILE).unlink()

    assert cli.main(["eval-public-set", "--out", str(tmp_path / "test-set.md")]) == 1

    assert f"No report store at {tmp_path / cli.STORE_FILE}" in capsys.readouterr().err
    assert not (tmp_path / cli.STORE_FILE).exists()
    assert not (tmp_path / "test-set.md").exists()


def test_eval_public_set_help_says_titles_link_to_the_publishers_page(capsys):
    with pytest.raises(SystemExit):
        cli.main(["eval-public-set", "--help"])

    help_text = " ".join(capsys.readouterr().out.split())
    assert "the publisher's page for each report" in help_text
    assert "GOV.UK" not in help_text
