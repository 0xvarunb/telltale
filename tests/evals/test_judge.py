"""The sentence judge: one structured-output call per cited fact sentence. Synthetic data only,
and a fake client, so no model is ever called."""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from telltale.answer import NO_ANSWER
from telltale.evals.judge import (
    JUDGE_SYSTEM,
    UnsupportedJudgeModel,
    build_judge_request,
    judge_answer,
    judge_cost,
    judge_record,
    judge_requests,
    parse_verdict,
    require_judge_model,
    unjudged_record,
)
from telltale.ingest.store import OCR_NOTE
from telltale.pricing import PRICES, UnknownModelPrice
from telltale.settings import DEFAULT_JUDGE_MODEL

MODEL = DEFAULT_JUDGE_MODEL
SOURCE_A = "https://assets.example/report-a.pdf#page=3"
SOURCE_B = "https://assets.example/report-b.pdf#page=7"


def cite(cited_text: str, source: str = SOURCE_A, title: str = "Synthetic report A") -> dict:
    return {"report_title": title, "source": source, "cited_text": cited_text}


def sentence(text: str, *citations: dict) -> dict:
    return {"text": text, "citations": list(citations)}


def saved(*sentences: dict) -> dict:
    """A saved answer in the shape `telltale.card.to_json` writes."""
    return {
        "question": "Synthetic question?",
        "sentences": list(sentences),
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.0001,
        "stop_reason": "end_turn",
        "searched": [],
    }


def verdict_reply(verdict, reason="Synthetic reason.", input_tokens=500, output_tokens=40,
                  stop_reason="end_turn"):
    """A structured-output reply: one text block holding the verdict as JSON."""
    return text_reply(
        json.dumps({"verdict": verdict, "reason": reason}),
        input_tokens=input_tokens, output_tokens=output_tokens, stop_reason=stop_reason,
    )


def text_reply(text="I think it is supported.", input_tokens=500, output_tokens=20,
               stop_reason="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text, citations=None)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        stop_reason=stop_reason,
    )


REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls, status: int, message: str = "Synthetic overload."):
    """An SDK status error, as the client raises it for an HTTP error reply."""
    return cls(message, response=httpx2.Response(status, request=REQUEST), body=None)


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(message="Synthetic network drop.", request=REQUEST)


class FakeMessages:
    """Hands back the canned replies in order; an exception in the list is raised instead."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeClient:
    def __init__(self, replies):
        self.messages = FakeMessages(replies)


def test_judge_system_text_is_exact():
    assert JUDGE_SYSTEM == (
        "You check one sentence from an answer against the report passages it cites.\n"
        "Reply with a verdict:\n"
        "- supported: every claim in the sentence is stated in the passages.\n"
        "- partly: some claims are stated, at least one is not, or the sentence makes a claim "
        "stronger or more certain than the passages.\n"
        "- not_supported: none of the sentence's claims are stated in the passages.\n"
        "Each passage is headed by the title of the report it comes from. Use that title only "
        "to tell which ship the passage is about, its type and the casualty the title names, "
        "even when a passage is only the title; "
        "the title does not back causes, the order of events or any other detail.\n"
        "Judge only against the passages. Do not use your own knowledge of ships, regulations "
        "or the casualty."
    )


def test_each_passage_is_shown_under_its_report_title():
    passages = [
        ("Fall into a hold on bulk carrier Synthetic Star", "The hatch was left open."),
        ("Grounding of tanker Synthetic Moon", "No watch was kept on the deck."),
    ]

    request = build_judge_request("The hatch was open and unwatched.", passages, MODEL)

    assert request["messages"][0]["content"] == (
        "Passage 1. Report title: Fall into a hold on bulk carrier Synthetic Star\n"
        "The hatch was left open.\n\n"
        "Passage 2. Report title: Grounding of tanker Synthetic Moon\n"
        "No watch was kept on the deck.\n\n"
        "Sentence:\nThe hatch was open and unwatched."
    )


def test_the_scanned_page_note_is_not_part_of_the_title_shown():
    title = "Grounding of tanker Synthetic Moon" + OCR_NOTE

    request = build_judge_request("A.", [(title, "B.")], MODEL)

    content = request["messages"][0]["content"]
    assert content.startswith("Passage 1. Report title: Grounding of tanker Synthetic Moon\nB.")
    assert OCR_NOTE.strip() not in content


def test_request_holds_sentence_and_every_cited_passage():
    passages = [("Synthetic report A", "The hatch was left open."),
                ("Synthetic report A", "No watch was kept on the deck.")]

    request = build_judge_request("The hatch was open and unwatched.", passages, MODEL)

    assert request["model"] == "claude-sonnet-5-5"
    assert request["max_tokens"] == 300
    assert request["system"] == JUDGE_SYSTEM
    assert not {"temperature", "top_p", "top_k"} & request.keys()

    # Claude Sonnet 5.5 refuses a forced tool call, so the verdict comes as structured output
    # (JSON in the reply text, held to the schema), with no tools in the request.
    assert "tools" not in request
    assert "tool_choice" not in request
    output_format = request["output_config"]["format"]
    assert output_format["type"] == "json_schema"
    schema = output_format["schema"]
    assert schema["type"] == "object"
    assert schema["properties"]["verdict"] == {
        "type": "string",
        "enum": ["supported", "partly", "not_supported"],
    }
    assert schema["properties"]["reason"]["type"] == "string"
    assert sorted(schema["required"]) == ["reason", "verdict"]
    assert schema["additionalProperties"] is False

    # Up-front thinking is off, so all 300 tokens are left for the verdict and its reason.
    assert request["thinking"] == {"type": "between_tools"}

    [message] = request["messages"]
    assert message["role"] == "user"
    sent = str(message["content"])
    assert "The hatch was open and unwatched." in sent
    for title, text in passages:
        assert title in sent and text in sent


def test_each_priced_judge_model_gets_its_own_thinking_off_setting():
    # The lowest thinking setting each model accepts differs: the other one is a 400 error.
    assert build_judge_request("A.", [("Report T", "B.")], "claude-sonnet-5-5")["thinking"] == {
        "type": "between_tools"
    }
    assert build_judge_request("A.", [("Report T", "B.")], "claude-haiku-5-5")["thinking"] == {
        "type": "disabled"
    }


def test_a_judge_model_with_no_thinking_off_setting_is_refused(monkeypatch):
    # Priced, so only the missing thinking setting is in the way.
    monkeypatch.setitem(PRICES, "claude-unlisted-test", (1.0, 1.0))
    with pytest.raises(UnsupportedJudgeModel, match="claude-unlisted-test"):
        build_judge_request("A.", [("Report T", "B.")], "claude-unlisted-test")
    with pytest.raises(UnsupportedJudgeModel, match="claude-unlisted-test"):
        require_judge_model("claude-unlisted-test")
    require_judge_model(MODEL)


@pytest.mark.parametrize("verdict", ["supported", "partly", "not_supported"])
def test_parses_each_verdict(verdict):
    assert parse_verdict(verdict_reply(verdict, "Because.")) == (verdict, "Because.")


def test_verdict_case_is_ignored():
    # Structured outputs do not hold enum values to their case.
    assert parse_verdict(verdict_reply("Not_supported", "Because.")) == (
        "not_supported", "Because."
    )


def test_unreadable_reply_is_unjudged():
    verdict, why = parse_verdict(text_reply())
    assert verdict == "unjudged"
    assert why

    for stray in ["unjudged", "mostly", "", None]:
        verdict, why = parse_verdict(verdict_reply(stray))
        assert verdict == "unjudged"
        assert why

    for text in ["[1, 2]", '"supported"', '{"verdict": "supported"', ""]:
        verdict, why = parse_verdict(text_reply(text))
        assert verdict == "unjudged", text
        assert why

    no_text = verdict_reply("supported")
    no_text.content = []
    assert parse_verdict(no_text)[0] == "unjudged"


def test_a_cut_off_or_refused_reply_is_unjudged():
    cut = text_reply('{"verdict": "supported", "reason": "The pass', stop_reason="max_tokens")
    verdict, why = parse_verdict(cut)
    assert verdict == "unjudged"
    assert "max_tokens" in why

    # A refusal may not hold to the schema, so even a readable verdict is not trusted.
    refused = verdict_reply("supported", stop_reason="refusal")
    verdict, why = parse_verdict(refused)
    assert verdict == "unjudged"
    assert "refus" in why


@pytest.mark.parametrize(
    "error",
    [
        status_error(anthropic.RateLimitError, 429),
        status_error(anthropic.InternalServerError, 500),
        status_error(anthropic.OverloadedError, 529),
        connection_error(),
    ],
    ids=["rate-limit", "server-error", "overloaded", "connection"],
)
def test_api_error_on_one_sentence_marks_it_unjudged_and_goes_on(error):
    """Review Focus 5: an error that could pass on a retry costs one sentence, not the run."""
    answer = saved(
        sentence("The hatch was left open overnight.", cite("The hatch was left open.")),
        sentence("No one kept watch on the deck.", cite("No watch was kept.")),
        sentence("The crew had not been briefed.", cite("There was no briefing.")),
    )
    client = FakeClient([verdict_reply("supported"), error, verdict_reply("partly")])

    verdicts, records, _ = judge_answer(answer, client, MODEL)

    assert len(client.messages.calls) == 3
    assert verdicts == {0: "supported", 1: "unjudged", 2: "partly"}
    assert [r["index"] for r in records] == [0, 1, 2]
    assert records[1]["verdict"] == "unjudged"
    assert str(error) in records[1]["reason"]
    assert records[1]["input_tokens"] == 0
    assert records[1]["output_tokens"] == 0


@pytest.mark.parametrize(
    "error",
    [
        status_error(anthropic.BadRequestError, 400, "Synthetic bad request."),
        status_error(anthropic.AuthenticationError, 401, "Synthetic bad key."),
        status_error(anthropic.PermissionDeniedError, 403, "Synthetic no access."),
        status_error(anthropic.NotFoundError, 404, "Synthetic no such model."),
    ],
    ids=["400", "401", "403", "404"],
)
def test_a_setup_error_is_not_swallowed_as_one_unjudged_sentence(error):
    """A request the API refuses outright would fail for every sentence, so it is raised."""
    answer = saved(
        sentence("The hatch was left open overnight.", cite("The hatch was left open.")),
        sentence("No one kept watch on the deck.", cite("No watch was kept.")),
    )
    client = FakeClient([error, verdict_reply("supported")])

    with pytest.raises(type(error)):
        judge_answer(answer, client, MODEL)
    assert len(client.messages.calls) == 1


def test_uncited_and_no_answer_sentences_are_not_sent():
    answer = saved(
        sentence("The hatch was left open overnight.", cite("The hatch was left open.")),
        sentence("This sentence cites nothing at all."),
        sentence(NO_ANSWER),
        sentence(
            "No one kept watch on the deck.",
            cite("No watch was kept."),
            cite("The deck was empty.", source=SOURCE_B, title="Synthetic report B"),
            cite("No watch was kept."),
        ),
    )
    client = FakeClient([verdict_reply("supported"), verdict_reply("not_supported")])

    verdicts, records, _ = judge_answer(answer, client, MODEL)

    assert verdicts == {0: "supported", 3: "not_supported"}
    assert [r["index"] for r in records] == [0, 3]
    assert len(client.messages.calls) == 2

    # The passages are the sentence's cited texts with their report titles, each once, in order.
    second = client.messages.calls[1]
    assert second == build_judge_request(
        "No one kept watch on the deck.",
        [("Synthetic report A", "No watch was kept."), ("Synthetic report B", "The deck was empty.")],
        MODEL,
    )


def test_cost_adds_up_from_usage():
    answer = saved(
        sentence("The hatch was left open overnight.", cite("The hatch was left open.")),
        sentence("No one kept watch on the deck.", cite("No watch was kept.")),
    )
    client = FakeClient(
        [
            verdict_reply("supported", input_tokens=1_000, output_tokens=100),
            verdict_reply("partly", input_tokens=2_000, output_tokens=50),
        ]
    )

    _, records, cost = judge_answer(answer, client, MODEL)

    assert [(r["input_tokens"], r["output_tokens"]) for r in records] == [(1_000, 100), (2_000, 50)]
    # 3,000 input tokens at $2 per million plus 150 output tokens at $10 per million.
    assert cost == pytest.approx(0.0075)


def test_answer_with_nothing_to_judge_costs_nothing():
    client = FakeClient([])

    verdicts, records, cost = judge_answer(saved(sentence(NO_ANSWER)), client, MODEL)

    assert (verdicts, records, cost) == ({}, [], 0.0)
    assert client.messages.calls == []


def test_real_sdk_client_sends_the_structured_output_request_and_reads_the_reply():
    """The real SDK over a mock transport (no network): the request goes out as built and the
    JSON reply text reads back as a verdict."""
    sent = []

    def handler(request):
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
                        "text": json.dumps(
                            {"verdict": "partly", "reason": "One claim is not stated."}
                        ),
                    }
                ],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1_000, "output_tokens": 100},
            },
        )

    client = anthropic.Anthropic(
        api_key="test-key",
        base_url="https://api.anthropic.com",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    answer = saved(sentence("The hatch was open and unwatched.", cite("The hatch was left open.")))

    verdicts, records, cost = judge_answer(answer, client, MODEL)

    [request] = sent
    body = json.loads(request.content)
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["thinking"] == {"type": "between_tools"}
    assert "tool_choice" not in body
    assert body["system"] == JUDGE_SYSTEM
    assert verdicts == {0: "partly"}
    assert records[0]["reason"] == "One claim is not stated."
    assert cost == pytest.approx(0.003)


def test_judge_cost_uses_the_price_of_the_model_passed():
    answer = saved(
        sentence("The hatch was left open overnight.", cite("The hatch was left open.")),
        sentence("No one kept watch on the deck.", cite("No watch was kept.")),
    )
    client = FakeClient(
        [
            verdict_reply("supported", input_tokens=1_000, output_tokens=100),
            verdict_reply("partly", input_tokens=2_000, output_tokens=50),
        ]
    )

    _, _, cost = judge_answer(answer, client, "claude-haiku-5-5")

    # 3,000 input tokens at $0.10 per million plus 150 output tokens at $0.50 per million.
    assert cost == pytest.approx(0.000375)


def test_a_judge_model_with_no_price_is_refused_before_any_call():
    answer = saved(sentence("The hatch was left open overnight.", cite("The hatch was open.")))
    client = FakeClient([verdict_reply("supported")])

    with pytest.raises(UnknownModelPrice, match="claude-unpriced-test"):
        judge_answer(answer, client, "claude-unpriced-test")
    assert client.messages.calls == []


def test_judge_requests_are_the_calls_judge_answer_makes():
    """Batch mode builds its judge requests with `judge_requests`; they match the live calls."""
    answer = saved(
        sentence("The hatch was left open overnight.", cite("The hatch was left open.")),
        sentence("This one has no source at all."),
        sentence(NO_ANSWER),
        sentence("No one kept watch on the deck.", cite("No watch was kept."), cite("No watch.")),
    )
    client = FakeClient([verdict_reply("supported"), verdict_reply("partly")])

    judge_answer(answer, client, MODEL)
    pairs = judge_requests(answer, MODEL)

    assert [index for index, _ in pairs] == [0, 3]
    assert [request for _, request in pairs] == client.messages.calls


def test_judge_records_and_batch_cost():
    record = judge_record(3, verdict_reply("partly", "One claim.", 1_000, 100))
    assert record == {
        "index": 3, "verdict": "partly", "reason": "One claim.",
        "input_tokens": 1_000, "output_tokens": 100,
    }
    missing = unjudged_record(4, "No result came back.")
    assert missing == {
        "index": 4, "verdict": "unjudged", "reason": "No result came back.",
        "input_tokens": 0, "output_tokens": 0,
    }
    live = judge_cost([record, missing], "claude-haiku-5-5")
    assert live == pytest.approx(0.00015)
    assert judge_cost([record, missing], "claude-haiku-5-5", batch=True) == pytest.approx(live / 2)
