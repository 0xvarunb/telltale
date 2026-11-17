import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from telltale.answer import SYSTEM_PROMPT, answer, build_request, parse_sentences
from telltale.answer.brief import NO_ANSWER, is_no_answer
from telltale.models import Chunk, Citation, Sentence

MODEL = "claude-haiku-5-5"
PDF_URL = "https://assets.publishing.service.gov.uk/media/test/report.pdf"
TITLE = "Report on the investigation of a tank entry on board a test bulk carrier"
SOURCE = f"{PDF_URL}#page=4"


def chunk(id_: int = 1, page: int = 4, passages: list[str] | None = None) -> Chunk:
    return Chunk(
        id=id_,
        report_slug=f"test-report-{id_}",
        report_title=TITLE,
        url=PDF_URL,
        page=page,
        passages=passages or ["The tank had not been ventilated.", "Two crew collapsed."],
    )


def cite(cited_text: str, source: str = SOURCE, title: str | None = f"{TITLE} (page 4)"):
    return SimpleNamespace(
        type="search_result_location", title=title, source=source, cited_text=cited_text
    )


def block(text: str, *citations) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text, citations=list(citations) or None)


def response(blocks, input_tokens=100, output_tokens=50, stop_reason="end_turn"):
    return SimpleNamespace(
        content=blocks,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        stop_reason=stop_reason,
    )


class FakeIndex:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.queries: list[tuple[str, int]] = []

    def search(self, query: str, k: int = 8) -> list[Chunk]:
        self.queries.append((query, k))
        return self.chunks[:k]


class FakeMessages:
    def __init__(self, resp):
        self.resp = resp
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.resp


class FakeClient:
    def __init__(self, resp):
        self.messages = FakeMessages(resp)


# Plan Step 1 tests


def test_request_shape():
    chunks = [chunk(1, page=4), chunk(2, page=5, passages=["The alarm sounded."])]

    request = build_request("Entering a ballast tank", chunks, MODEL)

    assert request["model"] == "claude-haiku-5-5"
    assert request["max_tokens"] == 8000
    assert request["system"] == SYSTEM_PROMPT
    assert not {"temperature", "top_p", "top_k"} & request.keys()
    [message] = request["messages"]
    assert message["role"] == "user"
    *results, question = message["content"]
    assert question == {"type": "text", "text": "Entering a ballast tank"}
    assert len(results) == 2
    for result, c in zip(results, chunks):
        assert result["type"] == "search_result"
        assert result["citations"] == {"enabled": True}
        assert result["source"] == c.source
        assert result["title"] == f"{TITLE} (page {c.page})"
        assert result["content"][0] == {
            "type": "text",
            "text": f"This passage is from the MAIB report: {TITLE}.",
        }
        assert result["content"][1:] == [{"type": "text", "text": p} for p in c.passages]


def test_sentence_spanning_two_blocks_gets_both_citations():
    first = cite("The tank had not been ventilated.")
    second = cite("Two crew collapsed.")
    blocks = [
        block("On the test bulk carrier, the tank had not been ventilated", first),
        block(" and two crew collapsed inside it.", second),
        block(" Nobody had tested the air before entry."),
    ]

    sentences = parse_sentences(blocks)

    assert [s.text for s in sentences] == [
        "On the test bulk carrier, the tank had not been ventilated"
        " and two crew collapsed inside it.",
        "Nobody had tested the air before entry.",
    ]
    assert [c.cited_text for c in sentences[0].citations] == [
        "The tank had not been ventilated.",
        "Two crew collapsed.",
    ]
    assert sentences[0].citations[0] == Citation(
        report_title=TITLE, source=SOURCE, cited_text="The tank had not been ventilated."
    )


def test_uncited_sentence_marked_unsourced():
    blocks = [
        block("The crew entered the tank without a gas test.", cite("No gas test was done.")),
        block(" This is a common pattern across many ships."),
    ]

    sentences = parse_sentences(blocks)

    assert sentences[0].sourced is True
    assert sentences[1].text == "This is a common pattern across many ships."
    assert sentences[1].sourced is False
    assert sentences[1].citations == []


def test_decimal_sentence_keeps_its_citation():
    blocks = [
        block("The vessel was 15.87m long.", cite("The vessel was 15.87m long.")),
        block(" She had loaded grain the day before."),
    ]

    sentences = parse_sentences(blocks)

    assert [s.text for s in sentences] == [
        "The vessel was 15.87m long.",
        "She had loaded grain the day before.",
    ]
    assert [c.cited_text for c in sentences[0].citations] == ["The vessel was 15.87m long."]
    assert sentences[1].sourced is False


def test_cost():
    client = FakeClient(
        response([block("The crew entered the tank alone.", cite("x"))], 20_000, 1_300)
    )

    result = answer("tank entry", FakeIndex([chunk()]), client, MODEL)

    assert result.input_tokens == 20_000
    assert result.output_tokens == 1_300
    assert result.cost_usd == pytest.approx(0.00265)


def test_prompt_keeps_rules():
    for phrase in (
        "Use only the search results provided",
        "no reliable answer in these reports",
        "No blame",
        "name the ship inside the first sentence about it",
    ):
        assert phrase in SYSTEM_PROMPT


def test_prompt_never_says_safe_to_proceed():
    # Spec rule 5: never "safe to proceed", never a stand-in for the ship's own process.
    assert "Never say or imply that a job is safe or safe to proceed" in SYSTEM_PROMPT
    assert "the permit to work, the risk assessment, the SMS or the office's permission" in (
        SYSTEM_PROMPT
    )


# Extras


def test_prompt_forbids_stretching_an_unrelated_case():
    assert "Do not offer the closest case" in SYSTEM_PROMPT


def test_prompt_judges_by_the_job_not_the_ship_type():
    # Ruling 27: a ballast-tank entry on a container ship answers a bulk-carrier question.
    assert "not by the type of ship" in SYSTEM_PROMPT
    assert "say what type of ship it was" in SYSTEM_PROMPT


def test_prompt_asks_for_the_exact_no_answer_sentence():
    assert f'exactly this sentence and nothing else: "{NO_ANSWER}"' in SYSTEM_PROMPT


def test_answer_searches_calls_model_and_parses():
    chunks = [chunk(1), chunk(2, page=5)]
    index = FakeIndex(chunks)
    client = FakeClient(
        response(
            [block("Two crew collapsed in the unventilated tank.", cite("Two crew collapsed."))],
            stop_reason="end_turn",
        )
    )

    result = answer("Entering a ballast tank", index, client, MODEL)

    assert index.queries == [("Entering a ballast tank", 8)]
    [call] = client.messages.calls
    assert call == build_request("Entering a ballast tank", chunks, MODEL)
    assert result.question == "Entering a ballast tank"
    assert result.chunks == chunks
    assert result.stop_reason == "end_turn"
    assert [s.text for s in result.sentences] == ["Two crew collapsed in the unventilated tank."]
    assert result.sentences[0].sourced


def test_no_chunks_skips_the_model():
    client = FakeClient(response([]))

    result = answer("replace a crankcase relief valve", FakeIndex([]), client, MODEL)

    assert client.messages.calls == []
    assert result.chunks == []
    assert [s.text for s in result.sentences] == [NO_ANSWER]
    assert "no reliable answer in these reports" in NO_ANSWER
    assert result.sentences[0].sourced is False
    assert (result.input_tokens, result.output_tokens, result.cost_usd) == (0, 0, 0.0)
    assert result.stop_reason == "no_results"


def test_newlines_break_headings_and_list_items():
    blocks = [
        block("## Tank entry on the test bulk carrier\n"),
        block("- The tank had not been ventilated", cite("The tank had not been ventilated.")),
        block("\n- Two crew collapsed inside the tank\n", cite("Two crew collapsed.")),
        block("1. The alarm was raised by the bosun on deck\n"),
    ]

    sentences = parse_sentences(blocks)

    assert [s.text for s in sentences] == [
        "Tank entry on the test bulk carrier",
        "The tank had not been ventilated",
        "Two crew collapsed inside the tank",
        "The alarm was raised by the bosun on deck",
    ]
    assert [len(s.citations) for s in sentences] == [0, 1, 1, 0]
    assert sentences[1].citations[0].cited_text == "The tank had not been ventilated."
    assert sentences[2].citations[0].cited_text == "Two crew collapsed."


def test_markdown_debris_and_short_fragments_dropped():
    blocks = [
        block("**"),
        block("The **chief officer** entered the tank first.", cite("The chief officer entered.")),
        block("**\n\n###\n\n* *\n\nSee below.\n\n"),
        block("Nobody had *tested* the air before entry."),
    ]

    sentences = parse_sentences(blocks)

    assert [s.text for s in sentences] == [
        "The chief officer entered the tank first.",
        "Nobody had tested the air before entry.",
    ]
    assert sentences[0].sourced and not sentences[1].sourced


def test_identical_citations_deduplicated_per_sentence():
    same = cite("Two crew collapsed.")
    blocks = [
        block("Two crew collapsed", same),
        block(" inside the tank", cite("Two crew collapsed.")),
        block(" within minutes.", cite("The alarm sounded.")),
    ]

    [sentence] = parse_sentences(blocks)

    assert [c.cited_text for c in sentence.citations] == [
        "Two crew collapsed.",
        "The alarm sounded.",
    ]


def test_report_title_from_chunk_by_source_or_stripped_title():
    blocks = [block("The tank had not been ventilated before entry.", cite("x", title=None))]
    [from_chunk] = parse_sentences(blocks, chunks=[chunk()])
    assert from_chunk.citations[0].report_title == TITLE

    blocks = [block("The tank had not been ventilated before entry.", cite("x"))]
    [from_title] = parse_sentences(blocks)
    assert from_title.citations[0].report_title == TITLE


def test_non_text_blocks_ignored():
    blocks = [
        SimpleNamespace(type="thinking", thinking="..."),
        block("The tank had not been ventilated before entry.", cite("x")),
    ]

    [sentence] = parse_sentences(blocks)

    assert sentence.sourced


def test_initial_at_sentence_end_does_not_take_the_next_citation():
    # Task 3 review: "hold B. The fire spread." used to stay one sentence.
    blocks = [
        block("The fire started in the cargo hold B.", cite("The fire started in hold B.")),
        block(" The crew then fought it for an hour."),
    ]

    sentences = parse_sentences(blocks)

    assert [s.text for s in sentences] == [
        "The fire started in the cargo hold B.",
        "The crew then fought it for an hour.",
    ]
    assert sentences[0].sourced and not sentences[1].sourced


def test_initial_before_a_name_like_word_still_merges():
    # Known limit: after a single capital, only common sentence openers start a new sentence.
    blocks = [
        block("The fire started in the cargo hold B.", cite("The fire started in hold B.")),
        block(" Crew members fought it for an hour."),
    ]

    [sentence] = parse_sentences(blocks)

    assert sentence.text == (
        "The fire started in the cargo hold B. Crew members fought it for an hour."
    )
    assert sentence.sourced


def test_real_sdk_round_trip_without_network():
    """The request validates through the installed SDK and its parsed citations map cleanly."""
    # The SDK sends through httpx2, which respx does not patch; a mock transport never touches
    # the network.
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": MODEL,
                "content": [
                    {
                        "type": "text",
                        "text": "Two crew collapsed in the tank.",
                        "citations": [
                            {
                                "type": "search_result_location",
                                "cited_text": "Two crew collapsed.",
                                "source": SOURCE,
                                "title": f"{TITLE} (page 4)",
                                "search_result_index": 0,
                                "start_block_index": 2,
                                "end_block_index": 3,
                            }
                        ],
                    },
                    {"type": "text", "text": " It was a quiet day otherwise."},
                ],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 20_000, "output_tokens": 1_300},
            },
        )

    client = anthropic.Anthropic(
        api_key="test-key",
        base_url="https://api.anthropic.com",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )

    result = answer("tank entry", FakeIndex([chunk()]), client, MODEL)

    [request] = sent
    assert request.url == "https://api.anthropic.com/v1/messages"
    body = json.loads(request.content)
    assert not {"temperature", "top_p", "top_k"} & body.keys()
    assert body["messages"][0]["content"][0]["type"] == "search_result"
    assert result.sentences[0].citations == [
        Citation(report_title=TITLE, source=SOURCE, cited_text="Two crew collapsed.")
    ]
    assert result.sentences[1].sourced is False
    assert result.cost_usd == pytest.approx(0.00265)


def test_bold_markers_do_not_merge_sentences():
    blocks = [
        block("**On the Test Ship, two crew died.**", cite("Two crew died.")),
        block(" The company had no entry procedure in place at all."),
    ]

    sentences = parse_sentences(blocks)

    assert [s.text for s in sentences] == [
        "On the Test Ship, two crew died.",
        "The company had no entry procedure in place at all.",
    ]
    assert sentences[0].sourced and not sentences[1].sourced

    blocks = [
        block("Two crew collapsed in the tank.", cite("Two crew collapsed.")),
        block(" **The Test Ship** later sank in heavy weather."),
    ]

    sentences = parse_sentences(blocks)

    assert [s.text for s in sentences] == [
        "Two crew collapsed in the tank.",
        "The Test Ship later sank in heavy weather.",
    ]
    assert sentences[0].sourced and not sentences[1].sourced


def test_reply_with_no_sentences_says_no_reliable_answer():
    client = FakeClient(response([block("**"), block("\n\n")], 1_000, 20, "max_tokens"))

    result = answer("tank entry", FakeIndex([chunk()]), client, MODEL)

    assert len(client.messages.calls) == 1
    assert [s.text for s in result.sentences] == [NO_ANSWER]
    assert result.sentences[0].sourced is False
    assert (result.input_tokens, result.output_tokens) == (1_000, 20)
    assert result.cost_usd == pytest.approx(1_000 / 1e6 * 0.10 + 20 / 1e6 * 0.50)
    assert result.stop_reason == "max_tokens"
    assert result.chunks == [chunk()]


def test_is_no_answer():
    cite = Citation(
        report_title="Test report",
        source="https://example.test/r.pdf#page=1",
        cited_text="The line parted.",
    )
    assert is_no_answer(Sentence(text=NO_ANSWER, citations=[]))
    assert is_no_answer(
        Sentence(text="There is no reliable answer in these reports for that job.", citations=[])
    )
    assert not is_no_answer(Sentence(text="The mooring line parted under load.", citations=[]))
    cited = Sentence(text="There is no reliable answer in these reports.", citations=[cite])
    assert not is_no_answer(cited)
    for wording in [
        "There is no reliable answer in these reports.",
        "there's no reliable answer to this question in the reports",
        "No reliable answer in these reports about that job.",
    ]:
        assert is_no_answer(Sentence(text=wording, citations=[])), wording


def test_no_answer_with_a_claim_is_not_a_no_answer():
    mixed = Sentence(
        text="There is no reliable answer in these reports, but a gas test must show 20.9% "
        "oxygen before entry.",
        citations=[],
    )
    assert not is_no_answer(mixed)
    assert not is_no_answer(Sentence(text=NO_ANSWER + " Always test the air.", citations=[]))


def test_title_without_a_chunk_drops_the_gov_uk_page_suffix():
    page = "https://www.gov.uk/maib-reports/test-report"
    raw = cite("The pilot over-corrected.", source=page, title="Preliminary examination: Test (GOV.UK page)")
    [sentence] = parse_sentences([block("The pilot over-corrected the heading in the channel.", raw)])
    assert sentence.citations[0].report_title == "Preliminary examination: Test"


def test_prompt_asks_for_a_citation_on_every_sentence_and_no_comment_on_the_results():
    # Ruling 28: the second real answer had 7 of 14 sentences unsourced, 3 of them about the results.
    assert "carry its citation, even when the sentence before it cites the same passage" in (
        SYSTEM_PROMPT
    )
    assert "Keep the report's own words for its findings and for how sure it is" in SYSTEM_PROMPT
    assert "never about the search results themselves" in SYSTEM_PROMPT
    assert "If you say what the results do not cover" not in SYSTEM_PROMPT


def test_prompt_targets_the_baseline_patterns():
    # Ruling 29: the 6 Nov baseline had paraphrase-then-quote pairs, title-only sentences, a
    # figure caption cited as a sentence, and an equipment definition from the model's knowledge.
    for phrase in (
        "Make each point once",
        "Never write a sentence that only repeats a report's title",
        "Leave out figure labels and captions",
        "Never explain a term, a piece of equipment or a procedure unless a result explains it",
    ):
        assert phrase in SYSTEM_PROMPT
