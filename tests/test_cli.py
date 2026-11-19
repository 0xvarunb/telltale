import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from helpers import HashEmbedder
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
    assert "Other report types left out: 1 (overseas-report 1)" in out
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
    assert "search result 4: search result has no link\n    no page link" in dropped


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
         "report_slug": "test-report-a"},
        {"report_title": "Test report b", "page": 7, "source": pdf.format("b", 7),
         "report_slug": "test-report-b"},
        {"report_title": "Test report a", "page": 9, "source": pdf.format("a", 9),
         "report_slug": "test-report-a"},
        {"report_title": "Test report c", "page": 1, "source": pdf.format("c", 1),
         "report_slug": "test-report-c"},
        {"report_title": "Test report d", "page": 2, "source": pdf.format("d", 2),
         "report_slug": "test-report-d"},
        {"report_title": "Test report e", "page": 5, "source": pdf.format("e", 5),
         "report_slug": "test-report-e"},
        {"report_title": "Test report f", "page": 8, "source": pdf.format("f", 8),
         "report_slug": "test-report-f"},
        # A GOV.UK page has no page numbers, so none is recorded.
        {
            "report_title": "Test report g",
            "page": None,
            "source": "https://www.gov.uk/maib-reports/test-report-g",
            "report_slug": "test-report-g",
        },
    ]
