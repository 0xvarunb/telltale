"""The sentence judge: one model call per cited fact sentence, asking whether the passages it
cites back it.

The verdict comes back as structured output: JSON in the reply text, held by the API to a
schema with a verdict and a short reason. A forced tool call is not used, since Claude Sonnet
5.5 refuses one with a 400 error (its "What's new" page, read 19 Nov 2026). Up-front thinking is
turned off, so `max_tokens` covers only the verdict.

A reply that cannot be read, or an error that could pass on a retry (rate limit, network,
server error), makes that one sentence `unjudged` and the run goes on. Any other API error, such
as a request the API refuses as invalid, is raised, since it would fail for every sentence.
Uncited sentences and no-answer sentences are never sent.

Verdicts are keyed by sentence index (0-based, the sentence's position in the saved answer's
`sentences` list), as `telltale.evals.score.score_question` reads them.
"""

import json

import anthropic

from telltale.ingest.store import OCR_NOTE
from telltale.answer import is_no_answer
from telltale.evals.score import Verdict
from telltale.models import Citation, Sentence
from telltale.pricing import cost_usd, require_price
from telltale.thinking import THINKING_OFF, UnsupportedModel, thinking_off

MAX_TOKENS = 300
# What the judge may answer. "unjudged" is ours alone, for a sentence with no readable verdict.
JUDGE_VERDICTS = ("supported", "partly", "not_supported")

JUDGE_SYSTEM = (
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

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(JUDGE_VERDICTS)},
        "reason": {"type": "string", "description": "One short line saying why."},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}

# Errors that could pass on a retry. The SDK has already retried these by the time one reaches
# us, so the sentence is left unjudged and the run goes on.
RETRYABLE_ERRORS = (anthropic.RateLimitError, anthropic.APIConnectionError)


class UnsupportedJudgeModel(UnsupportedModel):
    """Raised for a judge model with no known setting that turns its thinking off
    (`THINKING_OFF` in `telltale.thinking`)."""


def is_retryable(error: BaseException) -> bool:
    """True for a rate limit, a network failure or a server error (status 500 or above)."""
    if isinstance(error, RETRYABLE_ERRORS):
        return True
    return isinstance(error, anthropic.APIStatusError) and error.status_code >= 500


def require_judge_model(model: str) -> None:
    """Refuse a judge model with no price or no thinking-off setting, before any call."""
    require_price(model)
    try:
        thinking_off(model)
    except UnsupportedModel:
        raise UnsupportedJudgeModel(
            f"The judge model {model} has no known setting that turns its thinking off, so "
            f"{MAX_TOKENS} tokens may not hold its verdict. Add it to THINKING_OFF in "
            f"telltale/thinking.py first (listed now: {', '.join(THINKING_OFF)})."
        ) from None


def build_judge_request(sentence: str, passages: list[tuple[str, str]], model: str) -> dict:
    """Arguments for `client.messages.create`: the cited passages, numbered, each under its
    report title (`passages` holds (report title, cited text) pairs), then the sentence, with the
    verdict schema as the reply format and thinking turned off.

    The title goes with each passage because an answer names the ship from the report it cites,
    and that name is often in the title only.

    Never sends temperature, top_p or top_k.
    """
    if model not in THINKING_OFF:
        require_judge_model(model)
    numbered = "\n\n".join(
        f"Passage {n}. Report title: {title.removesuffix(OCR_NOTE)}\n{text}"
        for n, (title, text) in enumerate(passages, start=1)
    )
    content = f"{numbered}\n\nSentence:\n{sentence}"
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": JUDGE_SYSTEM,
        "thinking": thinking_off(model),
        "output_config": {"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
        "messages": [{"role": "user", "content": content}],
    }


def parse_verdict(response) -> tuple[Verdict, str]:
    """The verdict and reason from the judge's reply, or `("unjudged", why)` when there is no
    readable verdict. A missing or non-text reason is kept as an empty string.

    A refused reply is unjudged even if it reads, since a refusal may not hold to the schema.
    The verdict's case is ignored, since structured outputs do not hold enum values to it.
    """
    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        return "unjudged", "The judge refused to give a verdict (stop reason: refusal)."
    text = "".join(
        getattr(block, "text", "") or ""
        for block in getattr(response, "content", None) or []
        if getattr(block, "type", None) == "text"
    )
    try:
        given = json.loads(text)
    except json.JSONDecodeError:
        return "unjudged", f"The reply held no readable verdict (stop reason: {stop})."
    if not isinstance(given, dict):
        return "unjudged", f"The reply held no readable verdict (stop reason: {stop})."
    verdict = given.get("verdict")
    if not isinstance(verdict, str) or verdict.lower() not in JUDGE_VERDICTS:
        return "unjudged", f"The judge gave a verdict outside the list: {verdict!r}."
    reason = given.get("reason")
    return verdict.lower(), reason if isinstance(reason, str) else ""


def _passages(sentence: Sentence) -> list[tuple[str, str]]:
    """The sentence's (report title, cited text) pairs, each once, in order."""
    return list(dict.fromkeys((c.report_title, c.cited_text) for c in sentence.citations))


def judge_requests(saved: dict, model: str) -> list[tuple[int, dict]]:
    """(sentence index, request) for every cited fact sentence of a saved answer, in order.

    Uncited sentences and no-answer sentences are left out. `judge_answer` sends these one at a
    time; batch mode sends them all in one batch.
    """
    pairs = []
    for index, raw in enumerate(saved["sentences"]):
        sentence = Sentence(
            text=raw["text"], citations=[Citation(**c) for c in raw["citations"]]
        )
        if not sentence.sourced or is_no_answer(sentence):
            continue
        pairs.append((index, build_judge_request(sentence.text, _passages(sentence), model)))
    return pairs


def judge_record(index: int, response) -> dict:
    """The record of one judge reply: its verdict, reason and token usage."""
    verdict, reason = parse_verdict(response)
    usage = getattr(response, "usage", None)
    return {
        "index": index,
        "verdict": verdict,
        "reason": reason,
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }


def unjudged_record(index: int, reason: str) -> dict:
    """The record of a sentence that got no reply to read, so no verdict and no cost."""
    return {
        "index": index,
        "verdict": "unjudged",
        "reason": reason,
        "input_tokens": 0,
        "output_tokens": 0,
    }


def judge_cost(records: list[dict], model: str, *, batch: bool = False) -> float:
    """What these judge calls cost in US dollars on `model`, at the batch price if `batch`."""
    return sum(
        (cost_usd(model, r["input_tokens"], r["output_tokens"], batch=batch) for r in records),
        0.0,
    )


def judge_answer(
    saved: dict, client, model: str
) -> tuple[dict[int, Verdict], list[dict], float]:
    """Judge every cited fact sentence of a saved answer (the dict `telltale.card.to_json`
    writes).

    Returns the verdicts by sentence index, one record per call
    (`{index, verdict, reason, input_tokens, output_tokens}`), and the judge cost in US dollars,
    priced for `model`. A model with no price (`UnknownModelPrice`) or no thinking-off setting
    (`UnsupportedJudgeModel`) is refused before any call.

    An error that could pass on a retry leaves that sentence unjudged; any other error is raised.
    """
    require_judge_model(model)
    records: list[dict] = []
    for index, request in judge_requests(saved, model):
        try:
            response = client.messages.create(**request)
        except anthropic.APIError as error:
            if not is_retryable(error):
                raise
            records.append(unjudged_record(index, f"API error: {type(error).__name__}: {error}"))
        else:
            records.append(judge_record(index, response))
    verdicts: dict[int, Verdict] = {r["index"]: r["verdict"] for r in records}
    return verdicts, records, judge_cost(records, model)
