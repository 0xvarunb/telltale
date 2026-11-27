"""Ask the model for a cited answer and give each sentence the passages it cites."""

import re

import anthropic

from telltale.answer.prompt import NO_ANSWER, build_request
from telltale.models import Answer, Chunk, Citation, Sentence
from telltale.pricing import cost_usd, require_price
from telltale.search import HybridIndex
from telltale.text import sentence_spans

SEARCH_K = 8
MIN_WORDS = 4
NO_RESULTS = "no_results"
# How many reports a no-answer result lists as searched.
REPORTS_SEARCHED = 5
SEARCHED_CLOSING = "These were searched and did not match the job. They are not an answer."

# What the command line and the page say about how the model's reply ended.
REFUSED = "The model declined to answer this question."
CUT_OFF = "The answer was cut off at the length limit, so the last sentence may be incomplete."
CUT_OFF_EMPTY = "The answer was cut off at the length limit before a complete sentence."

_WORD = re.compile(r"\w+")
_PAGE_SUFFIX = re.compile(r"\s*\((?:page \d+|GOV\.UK page)\)$")
_HEADING = re.compile(r"^#+\s*")
_LIST_MARK = re.compile(r"^(?:[-+•]|\d+[.)])\s+")
# The whole sentence, nothing more: "There is no reliable answer in these reports for that job."
_NO_ANSWER_WORDING = re.compile(
    r"(?:there is |there's )?no reliable answer (?:to (?:this|that|your) question )?"
    r"in (?:these|the) reports(?: (?:for|about|on) (?:this|that|the) job)?\.?",
    re.IGNORECASE,
)


def _line_spans(text: str) -> list[tuple[int, int]]:
    """Sentence offsets in `text`, where every line break also ends a sentence.

    Model output puts headings and list items on their own lines, often with no full stop.
    """
    spans = []
    offset = 0
    for line in text.split("\n"):
        spans += [(offset + start, offset + end) for start, end in sentence_spans(line)]
        offset += len(line) + 1
    return spans


def _clean(raw: str) -> str:
    """Drop markdown markers (`*` anywhere, a leading `#` or list mark) and collapse whitespace."""
    text = raw.replace("*", "").strip()
    text = _HEADING.sub("", text)
    text = _LIST_MARK.sub("", text)
    return " ".join(text.split())


def _citation(raw, by_source: dict[str, Chunk]) -> Citation | None:
    """A search-result citation as a `Citation`; anything without a source is skipped.

    The report title and publisher come from the retrieved chunk with that source when known;
    otherwise the title is the result title with its " (page N)" or " (GOV.UK page)" suffix
    removed, and the publisher is `Citation`'s default (MAIB). Every source the model is sent is a
    retrieved chunk's, so only a caller that passes no chunks gets the fallback.
    """
    source = getattr(raw, "source", None)
    if not source:
        return None
    chunk = by_source.get(source)
    title = (chunk and chunk.report_title) or _PAGE_SUFFIX.sub(
        "", getattr(raw, "title", None) or ""
    )
    citation = Citation(report_title=title, source=source, cited_text=raw.cited_text)
    if chunk is not None:
        citation.source_id = chunk.source_id
    return citation


def parse_sentences(blocks: list, chunks: list[Chunk] | None = None) -> list[Sentence]:
    """Sentences of the response's text blocks, each with the citations of every block it overlaps.

    Identical citations are kept once per sentence. Fragments under four words are dropped.
    """
    by_source = {chunk.source: chunk for chunk in chunks or []}
    full = ""
    block_spans: list[tuple[int, int, list[Citation]]] = []
    for block in blocks:
        if getattr(block, "type", None) != "text":
            continue
        cites = [c for raw in block.citations or [] if (c := _citation(raw, by_source))]
        block_spans.append((len(full), len(full) + len(block.text), cites))
        full += block.text

    # Bold markers would hide a sentence end from the splitter ("died.** The"); a space keeps
    # every offset, and `_clean` drops the `*` from the text itself.
    sentences = []
    for start, end in _line_spans(full.replace("*", " ")):
        text = _clean(full[start:end])
        if len(_WORD.findall(text)) < MIN_WORDS:
            continue
        citations: list[Citation] = []
        seen = set()
        for a, z, cites in block_spans:
            if a < end and z > start:
                for c in cites:
                    key = (c.report_title, c.source, c.cited_text)
                    if key not in seen:
                        seen.add(key)
                        citations.append(c)
        sentences.append(Sentence(text=text, citations=citations))
    return sentences


def is_no_answer(sentence: Sentence) -> bool:
    """An unsourced sentence that only says there is no reliable answer (ours, or the model's
    close wording of it).

    It claims no fact, so it is shown plainly, without a "no source" mark. A sentence that says
    anything more is not one, so its other claims keep their mark.
    """
    if sentence.sourced:
        return False
    text = sentence.text.strip()
    return text == NO_ANSWER or _NO_ANSWER_WORDING.fullmatch(text) is not None


def is_no_answer_result(result: Answer) -> bool:
    """True when what is shown is only the no-answer sentence (or the model's wording of it).

    Uses the same rules as the page and the command line: `stop_notice` for a reply shown as a
    notice alone, and `is_no_answer` for each sentence.
    """
    _, notice_only = stop_notice(result)
    return not notice_only and bool(result.sentences) and all(
        is_no_answer(sentence) for sentence in result.sentences
    )


def reports_searched(chunks: list[Chunk], limit: int = REPORTS_SEARCHED) -> list[Chunk]:
    """The first retrieved chunk of each report, in retrieval order, up to `limit` reports."""
    first: dict[str, Chunk] = {}
    for chunk in chunks:
        if len(first) == limit:
            break
        first.setdefault(chunk.report_slug, chunk)
    return list(first.values())


def stop_notice(result: Answer) -> tuple[str | None, bool]:
    """What to say about how the reply ended, and whether that replaces the sentences.

    A refused or cut-off reply that left only our no-answer sentence is shown as the notice
    alone, since "no reliable answer" would be wrong there.
    """
    sentences = result.sentences
    fallback = len(sentences) == 1 and sentences[0].text == NO_ANSWER
    if result.stop_reason == "refusal":
        return REFUSED, fallback
    if result.stop_reason == "max_tokens":
        return (CUT_OFF_EMPTY, True) if fallback else (CUT_OFF, False)
    return None, False


def _no_answer() -> Sentence:
    return Sentence(text=NO_ANSWER, citations=[])


def no_results_answer(question: str) -> Answer:
    """The answer when search found no passage: the no-answer sentence, with no model call."""
    return Answer(
        question=question,
        sentences=[_no_answer()],
        chunks=[],
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        stop_reason=NO_RESULTS,
    )


def answer_from_response(
    question: str, chunks: list[Chunk], response, *, model: str, batch: bool = False
) -> Answer:
    """The cited answer in the model's reply to `build_request(question, chunks, model)`.

    A reply that leaves no sentences gets the no-answer sentence, with its real usage and
    stop_reason. The cost is priced for `model`, at the batch price when `batch` is true. Both
    `answer` and batch mode read replies here, so they parse them the same way.
    """
    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    sentences = parse_sentences(response.content, chunks=chunks)
    return Answer(
        question=question,
        sentences=sentences or [_no_answer()],
        chunks=chunks,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd(model, input_tokens, output_tokens, batch=batch),
        stop_reason=response.stop_reason or "",
    )


def answer(question: str, index: HybridIndex, client: anthropic.Anthropic, model: str) -> Answer:
    """Search, ask the model with the passages as search results, and parse the cited answer.

    With no passages found the model is not called: the answer is a single unsourced sentence
    saying there is no reliable answer, with stop_reason "no_results". A reply that leaves no
    sentences gets the same sentence, with its real usage and stop_reason.

    A model with no price is refused (`UnknownModelPrice`) before any search or call.
    """
    require_price(model)
    chunks = index.search(question, k=SEARCH_K)
    if not chunks:
        return no_results_answer(question)
    response = client.messages.create(**build_request(question, chunks, model))
    return answer_from_response(question, chunks, response, model=model)
