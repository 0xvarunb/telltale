"""Scoring one saved answer against its eval question, and totals over a run.

Pure functions: no model call, no file reading. A saved answer is the dict written by
`telltale.card.to_json`. Verdicts come from the judge, keyed by sentence index (0-based, the
sentence's position in the saved answer's `sentences` list).

Rules:
- A fact sentence is any sentence that is not a no-answer sentence (`is_no_answer`).
- Unsupported means an uncited fact sentence, or a cited one judged `partly` or `not_supported`.
  A cited fact sentence with no verdict, or verdict `unjudged`, is unjudged: it is reported
  apart and left out of the share, never counted as supported.
- A report question passes when at least one expected report is cited, unless the reply was
  refused. Other cited reports are listed but never count against it.
- A no-answer question passes only when the answer shows nothing but the no-answer sentence
  (`is_no_answer_result`).
- A citation maps to a report slug by its source, then by its report title without the OCR
  note, then to "?" when nothing searched matches.
"""

import re
from dataclasses import dataclass
from typing import Literal, get_args

from telltale.answer import is_no_answer, is_no_answer_result
from telltale.evals.questions import EvalQuestion
from telltale.ingest.store import OCR_NOTE
from telltale.models import Answer, Chunk, Citation, Sentence

Verdict = Literal["supported", "partly", "not_supported", "unjudged"]

VERDICTS = frozenset(get_args(Verdict))
UNKNOWN_SLUG = "?"

_PAGE_SUFFIX = re.compile(r"#page=\d+$")


@dataclass
class SentenceScore:
    index: int
    text: str
    cited: bool
    no_answer: bool
    verdict: Verdict | None  # None for an uncited or no-answer sentence


@dataclass
class QuestionScore:
    id: str
    expect: str
    no_answer_result: bool
    cited_slugs: list[str]  # every report cited, in order of first citation; "?" if unmatched
    retrieved_slugs: list[str]  # every report searched, in retrieval order
    expected_cited: list[str]
    expected_retrieved: list[str]
    passed: bool
    sentences: list[SentenceScore]
    cost_usd: float
    stop_reason: str
    expected_reports: list[str]  # the question's full expected list, for the run totals


@dataclass
class RunTotals:
    fact_sentences: int
    uncited: int
    partly: int
    not_supported: int
    unjudged: int
    unsupported: int
    unsupported_share: float
    report_questions: int
    report_questions_passed: int
    expected_reports: int
    expected_reports_retrieved: int
    no_answer_questions: int
    no_answer_questions_passed: int
    false_no_answers: int
    answer_cost_usd: float

    @property
    def cited(self) -> int:
        """Fact sentences with at least one citation: the ones sent to the judge."""
        return self.fact_sentences - self.uncited

    @property
    def headline_valid(self) -> bool:
        """False when more than MAX_UNJUDGED_PERCENT of the cited sentences are unjudged: the
        share would then leave out too much to stand for the run."""
        return self.unjudged * 100 <= self.cited * MAX_UNJUDGED_PERCENT


# The most of a run's cited sentences that may be unjudged for its headline share to stand.
MAX_UNJUDGED_PERCENT = 5


def _bare_title(title: str) -> str:
    """A report title without the OCR note, which only a report's scanned pages carry (the same
    rule `card.searched_from_json` uses)."""
    return title.removesuffix(OCR_NOTE)


def _chunk(n: int, item: dict) -> Chunk:
    """A searched entry as a chunk with no passages. A null page means the GOV.UK page."""
    title = item["report_title"]
    source = item["source"]
    page = item.get("page")
    web_page = page is None
    return Chunk(
        id=n,
        report_slug=item.get("report_slug") or _bare_title(title),
        report_title=title,
        url=source if web_page else _PAGE_SUFFIX.sub("", source),
        page=0 if web_page else page,
        passages=[],
        ocr=title.endswith(OCR_NOTE),
        web_page=web_page,
        source_id=item.get("source_id", "maib"),
    )


def answer_from_saved(saved: dict) -> Answer:
    """Rebuild the `Answer` a saved answer came from, with its chunks taken from `searched`."""
    return Answer(
        question=saved["question"],
        sentences=[
            Sentence(text=s["text"], citations=[Citation(**c) for c in s["citations"]])
            for s in saved["sentences"]
        ],
        chunks=[_chunk(n, item) for n, item in enumerate(saved.get("searched") or [])],
        input_tokens=saved["input_tokens"],
        output_tokens=saved["output_tokens"],
        cost_usd=saved["cost_usd"],
        stop_reason=saved["stop_reason"],
    )


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _slug_for(citation: Citation, chunks: list[Chunk]) -> str:
    for chunk in chunks:
        if chunk.source == citation.source:
            return chunk.report_slug
    title = _bare_title(citation.report_title)
    for chunk in chunks:
        if _bare_title(chunk.report_title) == title:
            return chunk.report_slug
    return UNKNOWN_SLUG


def score_question(q: EvalQuestion, saved: dict, verdicts: dict[int, Verdict]) -> QuestionScore:
    """Score one saved answer against its question, with the judge's verdicts by sentence index."""
    result = answer_from_saved(saved)

    sentences = []
    for index, sentence in enumerate(result.sentences):
        no_answer = is_no_answer(sentence)
        cited = sentence.sourced
        verdict = None
        if cited and not no_answer:
            # A missing verdict, or any label outside the four known ones, is unjudged, so a
            # stray judge reply is never counted as supported.
            given = verdicts.get(index, "unjudged")
            verdict = given if given in VERDICTS else "unjudged"
        sentences.append(
            SentenceScore(
                index=index, text=sentence.text, cited=cited, no_answer=no_answer, verdict=verdict
            )
        )

    cited_slugs = _unique(
        [_slug_for(c, result.chunks) for s in result.sentences for c in s.citations]
    )
    retrieved_slugs = _unique([chunk.report_slug for chunk in result.chunks])
    expected = list(q.expected_reports)
    expected_cited = [slug for slug in expected if slug in cited_slugs]
    expected_retrieved = [slug for slug in expected if slug in retrieved_slugs]
    no_answer_result = is_no_answer_result(result)

    if q.expect == "reports":
        passed = bool(expected_cited) and result.stop_reason != "refusal"
    else:
        passed = no_answer_result

    return QuestionScore(
        id=q.id,
        expect=q.expect,
        no_answer_result=no_answer_result,
        cited_slugs=cited_slugs,
        retrieved_slugs=retrieved_slugs,
        expected_cited=expected_cited,
        expected_retrieved=expected_retrieved,
        passed=passed,
        sentences=sentences,
        cost_usd=result.cost_usd,
        stop_reason=result.stop_reason,
        expected_reports=expected,
    )


def totals(scores: list[QuestionScore]) -> RunTotals:
    """Add up a run's scores."""
    facts = [s for score in scores for s in score.sentences if not s.no_answer]
    uncited = sum(1 for s in facts if not s.cited)
    partly = sum(1 for s in facts if s.verdict == "partly")
    not_supported = sum(1 for s in facts if s.verdict == "not_supported")
    unjudged = sum(1 for s in facts if s.cited and s.verdict in (None, "unjudged"))
    unsupported = uncited + partly + not_supported
    judged = len(facts) - unjudged

    reports = [score for score in scores if score.expect == "reports"]
    no_answers = [score for score in scores if score.expect == "no_answer"]
    return RunTotals(
        fact_sentences=len(facts),
        uncited=uncited,
        partly=partly,
        not_supported=not_supported,
        unjudged=unjudged,
        unsupported=unsupported,
        unsupported_share=unsupported / judged if judged else 0.0,
        report_questions=len(reports),
        report_questions_passed=sum(1 for score in reports if score.passed),
        expected_reports=sum(len(score.expected_reports) for score in reports),
        expected_reports_retrieved=sum(len(score.expected_retrieved) for score in reports),
        no_answer_questions=len(no_answers),
        no_answer_questions_passed=sum(1 for score in no_answers if score.passed),
        false_no_answers=sum(1 for score in reports if score.no_answer_result),
        answer_cost_usd=sum(score.cost_usd for score in scores),
    )
