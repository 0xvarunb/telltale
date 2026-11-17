import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from telltale.answer.brief import CUT_OFF, CUT_OFF_EMPTY, NO_ANSWER, REFUSED
from telltale.models import Answer, Citation, Sentence
from telltale.web import create_app

HEADING = "What went wrong on other ships doing this job?"
HINT = "Describe the job, the ship type and anything else that matters."
EMPTY = "Type the job you are about to do."
TOO_LONG = "Please keep it under 500 characters."
FAILED = "Sorry, no answer could be made just now."
OTHER_SITE = (
    "This question came from another website, so it was not asked. Ask it on this page."
)
FOOTER = (
    "Stage 1 of 6: RAG (retrieval-augmented generation). The tool first finds the report "
    "passages that match your job, then writes only from them. Reports: UK MAIB, Crown copyright."
)

LIMITS_LINK = '<a href="/limits">What this tool can\'t do</a>'
LIMITS_DOC = Path(__file__).parents[2] / "docs" / "limits.md"
# Sentences the limits page and docs/limits.md must both carry, word for word.
LIMITS_SENTENCES = [
    "What this tool can't do",
    "It covers MAIB merchant-ship reports only",
    "It knows nothing outside those reports",
    "It does not know about any accident that is not in those reports.",
    "Stage 1 marks these sentences; it does not yet remove them.",
    "The measured share of unsourced sentences in stage 1:",
    "It never says a job is safe, or \"safe to proceed\".",
    "It never replaces the permit to work, the risk assessment, the SMS or the office's permission.",
    "Reports with no PDF link and no page text, not searchable",
    "Crown copyright, used under the Open Government Licence v3.0.",
]

PDF_ALPHA = "https://assets.publishing.service.gov.uk/media/test/report-alpha.pdf"
PDF_BRAVO = "https://assets.publishing.service.gov.uk/media/test/report-bravo.pdf"
ALPHA = Citation(
    report_title="Test report alpha",
    source=f"{PDF_ALPHA}#page=4",
    cited_text="The bosun entered the tank without a gas test.",
)
ALPHA_2 = Citation(
    report_title="Test report alpha",
    source=f"{PDF_ALPHA}#page=4",
    cited_text="The tank had not been ventilated.",
)
BRAVO = Citation(
    report_title="Test report bravo",
    source=f"{PDF_BRAVO}#page=12",
    cited_text="The second officer collapsed at the foot of the ladder.",
)
SOURCED = "The bosun entered the ballast tank without a gas test."
UNSOURCED = "Gas tests are always required before entry."


def make_answer(sentences: list[Sentence], stop_reason: str = "end_turn") -> Answer:
    return Answer(
        question="entering a ballast tank",
        sentences=sentences,
        chunks=[],
        input_tokens=20_000,
        output_tokens=1_300,
        cost_usd=0.00265,
        stop_reason=stop_reason,
    )


class StubAnswer:
    """Records the questions it is asked and returns a fixed answer."""

    def __init__(self, result: Answer | None = None, error: Exception | None = None):
        self.result = result or make_answer([Sentence(text=SOURCED, citations=[ALPHA])])
        self.error = error
        self.questions: list[str] = []

    def __call__(self, question: str) -> Answer:
        self.questions.append(question)
        if self.error is not None:
            raise self.error
        return self.result


def client_for(stub: StubAnswer) -> TestClient:
    return TestClient(create_app(stub), raise_server_exceptions=False)


def sources_panel(html: str) -> str:
    match = re.search(r'<section class="sources".*?</section>', html, re.DOTALL)
    return match.group(0) if match else ""


def test_form_renders():
    response = client_for(StubAnswer()).get("/")

    assert response.status_code == 200
    assert HEADING in response.text
    assert HINT in response.text
    assert FOOTER in response.text
    assert 'name="question"' in response.text
    assert 'action="/ask"' in response.text


def test_footer_links_limits_page():
    client = client_for(StubAnswer())

    form = client.get("/").text
    answered = client.post("/ask", data={"question": "entering a ballast tank"}).text

    assert LIMITS_LINK in form
    assert LIMITS_LINK in answered


def _plain(html: str) -> str:
    """Page text without tags, entities for quotes undone, whitespace collapsed."""
    text = re.sub(r"<[^>]+>", "", html).replace("&#39;", "'").replace("&quot;", '"')
    return " ".join(text.split())


def test_limits_page_renders_the_limits():
    response = client_for(StubAnswer()).get("/limits")

    assert response.status_code == 200
    text = _plain(response.text)
    for sentence in LIMITS_SENTENCES:
        assert sentence in text
    # Every stage 1 figure is measured now (6 Nov 2026).
    assert "not yet measured" not in text
    assert "24 of 71 sentences (34%)" in text
    assert LIMITS_LINK in response.text


def _doc_text() -> str:
    """docs/limits.md without markdown markers (headings, list dashes, bold, code ticks),
    whitespace collapsed."""
    lines = LIMITS_DOC.read_text(encoding="utf-8").splitlines()
    text = " ".join(re.sub(r"^\s*(?:#+|-)\s+", "", line) for line in lines)
    return " ".join(text.replace("**", "").replace("`", "").split())


def test_limits_page_matches_docs():
    html = client_for(StubAnswer()).get("/limits").text
    article = re.search(r"<article.*?</article>", html, re.DOTALL).group(0)
    blocks = re.findall(r"<(h1|h2|p|li)\b[^>]*>(.*?)</\1>", article, re.DOTALL)
    doc = _doc_text()

    assert len(blocks) > 20
    # Every heading, paragraph and list item on the page is in the doc, word for word.
    for _, block in blocks:
        assert _plain(block) in doc
    for sentence in LIMITS_SENTENCES:
        assert sentence in doc


def test_answer_shows_sources_and_no_source_marker():
    stub = StubAnswer(make_answer([
        Sentence(text=SOURCED, citations=[ALPHA]),
        Sentence(text=UNSOURCED, citations=[]),
    ]))

    response = client_for(stub).post("/ask", data={"question": "entering a ballast tank"})

    assert response.status_code == 200
    html = response.text
    assert stub.questions == ["entering a ballast tank"]
    assert SOURCED in html
    assert UNSOURCED in html
    assert html.count('class="no-source"') == 1
    assert ">no source<" in html
    assert 'href="#source-1"' in html
    # The question stays in the box.
    assert re.search(r"<textarea[^>]*>entering a ballast tank</textarea>", html)
    panel = sources_panel(html)
    assert 'id="source-1"' in panel
    assert "Test report alpha" in panel
    assert "Page 4" in panel
    assert ALPHA.cited_text in panel
    assert f'href="{PDF_ALPHA}#page=4"' in panel
    assert 'rel="noopener"' in panel
    assert "Crown copyright" in panel
    assert "Cost $0.0027" in html


def test_empty_question_friendly():
    stub = StubAnswer()

    response = client_for(stub).post("/ask", data={"question": "   "})

    assert response.status_code == 200
    assert EMPTY in response.text
    assert stub.questions == []


def test_long_question_friendly():
    stub = StubAnswer()

    response = client_for(stub).post("/ask", data={"question": "a" * 501})

    assert response.status_code == 200
    assert TOO_LONG in response.text
    assert stub.questions == []
    # The question is kept so it can be shortened.
    assert "a" * 501 in response.text


def test_no_reliable_answer_renders():
    stub = StubAnswer(make_answer([Sentence(text=NO_ANSWER, citations=[])], "no_results"))

    response = client_for(stub).post("/ask", data={"question": "replace a crankcase relief valve"})

    assert response.status_code == 200
    html = response.text
    assert NO_ANSWER in html
    assert 'class="no-source"' not in html
    assert 'href="#source-' not in html
    assert sources_panel(html) == ""


def test_500_characters_is_accepted():
    stub = StubAnswer()

    response = client_for(stub).post("/ask", data={"question": "a" * 500})

    assert response.status_code == 200
    assert TOO_LONG not in response.text
    assert stub.questions == ["a" * 500]


def test_length_is_counted_after_trimming_spaces():
    stub = StubAnswer()

    response = client_for(stub).post("/ask", data={"question": "  " + "a" * 500 + "\n"})

    assert TOO_LONG not in response.text
    assert stub.questions == ["a" * 500]


def test_missing_question_field_is_treated_as_empty():
    stub = StubAnswer()

    response = client_for(stub).post("/ask", data={})

    assert response.status_code == 200
    assert EMPTY in response.text
    assert stub.questions == []


def test_answer_fn_failure_shows_a_friendly_message(caplog):
    stub = StubAnswer(error=RuntimeError("connection refused"))

    with caplog.at_level("ERROR"):
        response = client_for(stub).post("/ask", data={"question": "entering a ballast tank"})

    assert response.status_code == 200
    assert FAILED in response.text
    assert "connection refused" not in response.text
    assert "Traceback" not in response.text
    assert re.search(r"<textarea[^>]*>entering a ballast tank</textarea>", response.text)
    assert any(record.exc_info for record in caplog.records)


def test_script_in_a_sentence_and_a_passage_is_escaped():
    script = "<script>alert(1)</script>"
    cited = Citation(
        report_title="Test report <b>alpha</b>",
        source=f"{PDF_ALPHA}#page=4",
        cited_text=f"The tank {script} was closed.",
    )
    stub = StubAnswer(make_answer([Sentence(text=f"The hatch {script} was open.", citations=[cited])]))

    response = client_for(stub).post("/ask", data={"question": f"tank {script}"})

    html = response.text
    assert script not in html
    assert "<b>alpha</b>" not in html
    assert html.count("&lt;script&gt;alert(1)&lt;/script&gt;") == 3  # sentence, passage, box


def test_sources_are_numbered_in_order_and_deduplicated():
    stub = StubAnswer(make_answer([
        Sentence(text="The bosun went into the tank first.", citations=[ALPHA]),
        Sentence(text="The second officer followed him down.", citations=[BRAVO, ALPHA]),
        Sentence(text="The tank had not been ventilated at all.", citations=[ALPHA_2]),
    ]))

    html = client_for(stub).post("/ask", data={"question": "tank entry"}).text

    panel = sources_panel(html)
    assert panel.count('id="source-') == 2
    assert panel.index('id="source-1"') < panel.index("Test report alpha")
    assert panel.index("Test report alpha") < panel.index('id="source-2"')
    assert panel.index('id="source-2"') < panel.index("Test report bravo")
    # Both passages of the same page sit under its one entry, each once.
    assert panel.count(ALPHA.cited_text) == 1
    assert panel.count(ALPHA_2.cited_text) == 1
    assert "Page 12" in panel
    answer_part = html.split('<section class="sources"')[0]
    marks = re.findall(r'href="#source-(\d)"', answer_part)
    assert marks == ["1", "2", "1", "1"]


def test_only_cited_sources_are_listed():
    stub = StubAnswer(make_answer([
        Sentence(text=SOURCED, citations=[BRAVO]),
        Sentence(text=NO_ANSWER, citations=[]),
    ]))

    panel = sources_panel(client_for(stub).post("/ask", data={"question": "tank entry"}).text)

    assert "Test report bravo" in panel
    assert "Test report alpha" not in panel


def test_a_cut_off_answer_is_flagged():
    stub = StubAnswer(make_answer([Sentence(text=SOURCED, citations=[ALPHA])], "max_tokens"))

    html = client_for(stub).post("/ask", data={"question": "tank entry"}).text

    assert SOURCED in html
    assert CUT_OFF in html


@pytest.mark.parametrize(
    ("stop_reason", "message"), [("refusal", REFUSED), ("max_tokens", CUT_OFF_EMPTY)]
)
def test_refused_or_cut_off_replaces_the_no_answer_sentence(stop_reason, message):
    stub = StubAnswer(make_answer([Sentence(text=NO_ANSWER, citations=[])], stop_reason))

    html = client_for(stub).post("/ask", data={"question": "tank entry"}).text

    assert message in html
    assert NO_ANSWER not in html


def test_a_refusal_after_sentences_is_flagged():
    stub = StubAnswer(make_answer([Sentence(text=SOURCED, citations=[ALPHA])], "refusal"))

    html = client_for(stub).post("/ask", data={"question": "tank entry"}).text

    assert SOURCED in html
    assert REFUSED in html


def test_a_source_that_is_not_a_web_link_gets_no_link():
    odd = Citation(report_title="Test report alpha", source="javascript:alert(1)", cited_text="x y")
    stub = StubAnswer(make_answer([Sentence(text=SOURCED, citations=[odd])]))

    html = client_for(stub).post("/ask", data={"question": "tank entry"}).text

    assert 'href="javascript:' not in html
    assert "Test report alpha" in sources_panel(html)


def test_stylesheet_is_served_from_any_folder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # templates and css come from the package, not the cwd

    response = client_for(StubAnswer()).get("/static/style.css")

    assert response.status_code == 200
    assert "#f4f1ea" in response.text


def test_get_ask_goes_back_to_the_form():
    response = client_for(StubAnswer()).get("/ask", follow_redirects=False)

    assert response.status_code in (303, 307)
    assert response.headers["location"] == "/"


def test_a_field_over_one_megabyte_is_too_long():
    stub = StubAnswer()

    response = client_for(stub).post("/ask", data={"question": "a" * (1024 * 1024 + 1)})

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert TOO_LONG in response.text
    assert stub.questions == []


def test_a_file_in_the_question_field_is_treated_as_empty():
    stub = StubAnswer()

    response = client_for(stub).post(
        "/ask", files={"question": ("job.txt", b"entering a ballast tank", "text/plain")}
    )

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert EMPTY in response.text
    assert stub.questions == []


@pytest.mark.parametrize("invisible", ["​", "‌‍", "﻿​ ", "?!"])
def test_a_question_with_no_letters_or_digits_is_empty(invisible):
    stub = StubAnswer()

    response = client_for(stub).post("/ask", data={"question": invisible})

    assert EMPTY in response.text
    assert stub.questions == []


def test_a_post_from_another_website_is_refused():
    stub = StubAnswer()

    response = client_for(stub).post(
        "/ask",
        data={"question": "entering a ballast tank"},
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 200
    assert OTHER_SITE in response.text
    assert stub.questions == []


def test_a_post_from_a_sandboxed_page_is_refused():
    stub = StubAnswer()

    client_for(stub).post("/ask", data={"question": "tank entry"}, headers={"Origin": "null"})

    assert stub.questions == []


def test_a_post_from_this_page_is_answered():
    stub = StubAnswer()

    response = client_for(stub).post(
        "/ask", data={"question": "tank entry"}, headers={"Origin": "http://testserver"}
    )

    assert OTHER_SITE not in response.text
    assert stub.questions == ["tank entry"]


def test_a_post_with_no_origin_is_answered():
    stub = StubAnswer()

    client_for(stub).post("/ask", data={"question": "tank entry"})

    assert stub.questions == ["tank entry"]


def test_a_no_answer_sentence_that_adds_a_claim_is_marked():
    mixed = (
        "There is no reliable answer in these reports, but a gas test must show 20.9% oxygen "
        "before entry."
    )
    stub = StubAnswer(make_answer([Sentence(text=mixed, citations=[])]))

    html = client_for(stub).post("/ask", data={"question": "tank entry"}).text

    assert mixed in html
    assert html.count('class="no-source"') == 1
    assert ">no source<" in html
