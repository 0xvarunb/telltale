"""Scoring one saved answer against its question, and totals over a run. Synthetic data only."""

import pytest

from telltale.answer import NO_ANSWER
from telltale.evals.questions import EvalQuestion
from telltale.evals.score import (
    QuestionScore,
    RunTotals,
    SentenceScore,
    answer_from_saved,
    score_question,
    totals,
)
from telltale.ingest.store import OCR_NOTE

PDF_A = "https://assets.example/report-a.pdf"
PDF_B = "https://assets.example/report-b.pdf"
WEB_C = "https://www.gov.uk/maib-reports/report-c"


def question(
    expect: str = "reports", expected: tuple[str, ...] = ("slug-a",), id_: str = "Q1"
) -> EvalQuestion:
    return EvalQuestion(
        id=id_,
        question=f"Synthetic question {id_}?",
        expect=expect,  # type: ignore[arg-type]
        expected_reports=expected if expect == "reports" else (),
        related_reports=(),
        blind=False,
    )


def searched(title: str, source: str, slug: str, page: int | None) -> dict:
    return {"report_title": title, "page": page, "source": source, "report_slug": slug}


SEARCHED = [
    searched("Report A", f"{PDF_A}#page=3", "slug-a", 3),
    searched("Report B", f"{PDF_B}#page=7", "slug-b", 7),
    searched("Report A", f"{PDF_A}#page=4", "slug-a", 4),
]


def cite(title: str, source: str) -> dict:
    return {"report_title": title, "source": source, "cited_text": "A passage."}


def sentence(text: str, *citations: dict) -> dict:
    return {"text": text, "citations": list(citations)}


def saved(
    sentences: list[dict],
    searched_: list[dict] | None = None,
    stop_reason: str = "end_turn",
    cost: float = 0.01,
) -> dict:
    return {
        "question": "Synthetic question Q1?",
        "sentences": sentences,
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": cost,
        "stop_reason": stop_reason,
        "searched": SEARCHED if searched_ is None else searched_,
    }


NO_ANSWER_SAVED = saved([sentence(NO_ANSWER)])


def test_answer_from_saved_rebuilds_chunks_from_searched():
    result = answer_from_saved(
        saved(
            [sentence("A fact.", cite("Report A", f"{PDF_A}#page=3"))],
            searched_=[
                searched("Report A", f"{PDF_A}#page=3", "slug-a", 3),
                searched("Report C", WEB_C, "slug-c", None),
                searched("Report B" + OCR_NOTE, f"{PDF_B}#page=2", "slug-b", 2),
            ],
        )
    )
    assert [c.source for c in result.chunks] == [f"{PDF_A}#page=3", WEB_C, f"{PDF_B}#page=2"]
    assert [c.report_slug for c in result.chunks] == ["slug-a", "slug-c", "slug-b"]
    assert [c.source_page for c in result.chunks] == [3, None, 2]
    assert [c.ocr for c in result.chunks] == [False, False, True]
    assert result.sentences[0].citations[0].page == 3
    assert result.stop_reason == "end_turn"
    assert result.cost_usd == 0.01


def test_report_question_passes_when_an_expected_report_is_cited():
    score = score_question(
        question(),
        saved([sentence("A fact.", cite("Report A", f"{PDF_A}#page=4"))]),
        {0: "supported"},
    )
    assert score.passed is True
    assert score.expected_cited == ["slug-a"]
    assert score.cited_slugs == ["slug-a"]
    assert score.retrieved_slugs == ["slug-a", "slug-b"]
    assert score.expected_retrieved == ["slug-a"]
    assert score.no_answer_result is False


def test_other_cited_reports_are_listed_and_not_penalised():
    score = score_question(
        question(),
        saved(
            [
                sentence("A fact.", cite("Report B", f"{PDF_B}#page=7")),
                sentence("Another fact.", cite("Report A", f"{PDF_A}#page=3")),
            ]
        ),
        {0: "supported", 1: "supported"},
    )
    assert score.passed is True
    assert score.cited_slugs == ["slug-b", "slug-a"]
    assert score.expected_cited == ["slug-a"]


def test_report_question_fails_when_only_another_report_is_cited():
    score = score_question(
        question(),
        saved([sentence("A fact.", cite("Report B", f"{PDF_B}#page=7"))]),
        {0: "supported"},
    )
    assert score.passed is False
    assert score.expected_cited == []
    assert score.expected_retrieved == ["slug-a"]


def test_report_question_fails_on_no_answer():
    score = score_question(question(), NO_ANSWER_SAVED, {})
    assert score.no_answer_result is True
    assert score.passed is False
    assert score.cited_slugs == []


def test_refusal_on_report_question_is_a_miss():
    refused = saved([sentence(NO_ANSWER)], stop_reason="refusal")
    score = score_question(question(), refused, {})
    assert score.passed is False
    assert score.no_answer_result is False  # shown as the refusal notice, not "no reliable answer"
    assert score.stop_reason == "refusal"

    # A refusal is a miss even if the reply cited an expected report before it stopped.
    refused_cited = saved(
        [sentence("A fact.", cite("Report A", f"{PDF_A}#page=3"))], stop_reason="refusal"
    )
    score = score_question(question(), refused_cited, {0: "supported"})
    assert score.passed is False
    assert score.expected_cited == ["slug-a"]


def test_no_answer_question_passes_only_on_no_answer_result():
    q = question(expect="no_answer")
    score = score_question(q, NO_ANSWER_SAVED, {})
    assert score.passed is True
    assert score.no_answer_result is True

    cited = saved([sentence("A fact.", cite("Report B", f"{PDF_B}#page=7"))])
    score = score_question(q, cited, {0: "supported"})
    assert score.passed is False
    assert score.cited_slugs == ["slug-b"]

    refused = saved([sentence(NO_ANSWER)], stop_reason="refusal")
    assert score_question(q, refused, {}).passed is False


def test_no_results_answer_scores_without_crashing():
    empty = saved([sentence(NO_ANSWER)], searched_=[], stop_reason="no_results")
    score = score_question(question(expect="no_answer"), empty, {})
    assert score.passed is True
    assert score.retrieved_slugs == []


def test_web_page_citation_maps_to_slug():
    searched_ = [searched("Report C", WEB_C, "slug-c", None)]
    score = score_question(
        question(expected=("slug-c",)),
        saved([sentence("A fact.", cite("Report C", WEB_C))], searched_=searched_),
        {0: "supported"},
    )
    assert score.cited_slugs == ["slug-c"]
    assert score.passed is True


def test_ocr_and_pdf_pages_of_one_report_map_to_one_slug():
    searched_ = [
        searched("Report A" + OCR_NOTE, f"{PDF_A}#page=2", "slug-a", 2),
        searched("Report A", f"{PDF_A}#page=5", "slug-a", 5),
    ]
    score = score_question(
        question(),
        saved(
            [
                sentence("A fact.", cite("Report A" + OCR_NOTE, f"{PDF_A}#page=2")),
                sentence("Another fact.", cite("Report A", f"{PDF_A}#page=5")),
                # A source not in `searched` still maps by its title without the OCR note.
                sentence("A third fact.", cite("Report A" + OCR_NOTE, f"{PDF_A}#page=9")),
            ],
            searched_=searched_,
        ),
        {0: "supported", 1: "supported", 2: "supported"},
    )
    assert score.cited_slugs == ["slug-a"]
    assert score.retrieved_slugs == ["slug-a"]


def test_citation_matching_nothing_searched_is_unknown():
    score = score_question(
        question(),
        saved([sentence("A fact.", cite("Report Z", "https://assets.example/z.pdf#page=1"))]),
        {0: "supported"},
    )
    assert score.cited_slugs == ["?"]
    assert score.passed is False


def test_uncited_sentence_is_unsupported():
    score = score_question(
        question(),
        saved(
            [
                sentence("A fact.", cite("Report A", f"{PDF_A}#page=3")),
                sentence("An uncited claim."),
            ]
        ),
        {0: "supported"},
    )
    uncited = score.sentences[1]
    assert uncited == SentenceScore(
        index=1, text="An uncited claim.", cited=False, no_answer=False, verdict=None
    )
    run = totals([score])
    assert run.fact_sentences == 2
    assert run.uncited == 1
    assert run.unsupported == 1
    assert run.unsupported_share == pytest.approx(0.5)


def test_no_answer_sentence_is_not_a_fact_sentence():
    score = score_question(question(expect="no_answer"), NO_ANSWER_SAVED, {})
    assert score.sentences == [
        SentenceScore(index=0, text=NO_ANSWER, cited=False, no_answer=True, verdict=None)
    ]
    run = totals([score])
    assert run.fact_sentences == 0
    assert run.uncited == 0
    assert run.unsupported == 0
    assert run.unsupported_share == 0.0


def test_missing_verdict_counts_as_unjudged():
    score = score_question(
        question(),
        saved(
            [
                sentence("A fact.", cite("Report A", f"{PDF_A}#page=3")),
                sentence("Another fact.", cite("Report A", f"{PDF_A}#page=4")),
                sentence("A third fact.", cite("Report B", f"{PDF_B}#page=7")),
            ]
        ),
        {0: "supported", 2: "unjudged"},  # sentence 1 has no verdict at all
    )
    assert [s.verdict for s in score.sentences] == ["supported", "unjudged", "unjudged"]
    run = totals([score])
    assert run.unjudged == 2
    assert run.unsupported == 0
    # Unjudged sentences are left out of the share, never counted as supported.
    assert run.unsupported_share == 0.0
    assert run.fact_sentences == 3


def test_unknown_verdict_counts_as_unjudged():
    score = score_question(
        question(),
        saved(
            [
                sentence("A fact.", cite("Report A", f"{PDF_A}#page=3")),
                sentence("Another fact.", cite("Report A", f"{PDF_A}#page=4")),
                sentence("A third fact.", cite("Report B", f"{PDF_B}#page=7")),
            ]
        ),
        {0: "not supported", 1: "Supported", 2: "maybe"},  # type: ignore[dict-item]
    )
    assert [s.verdict for s in score.sentences] == ["unjudged", "unjudged", "unjudged"]
    run = totals([score])
    assert run.unjudged == 3
    assert run.unsupported == 0
    assert run.unsupported_share == 0.0


def test_cut_off_no_answer_alone_does_not_pass_a_no_answer_question():
    cut = saved([sentence(NO_ANSWER)], stop_reason="max_tokens")
    score = score_question(question(expect="no_answer"), cut, {})
    assert score.no_answer_result is False  # shown as the cut-off notice alone
    assert score.passed is False
    assert score.stop_reason == "max_tokens"


def test_cut_off_answer_still_scores_sentences():
    cut = saved(
        [
            sentence("A fact.", cite("Report A", f"{PDF_A}#page=3")),
            sentence("A half-finished claim."),
        ],
        stop_reason="max_tokens",
    )
    score = score_question(question(), cut, {0: "not_supported"})
    assert score.stop_reason == "max_tokens"
    assert score.passed is True
    assert [s.verdict for s in score.sentences] == ["not_supported", None]
    assert [s.cited for s in score.sentences] == [True, False]
    run = totals([score])
    assert run.fact_sentences == 2
    assert run.unsupported == 2


def sscore(index: int, cited: bool, verdict=None, no_answer: bool = False) -> SentenceScore:
    return SentenceScore(
        index=index, text=f"Sentence {index}.", cited=cited, no_answer=no_answer, verdict=verdict
    )


def test_totals():
    # Q1: report question, passed. Two expected reports, one retrieved.
    # Sentences: supported, partly, uncited.
    q1 = QuestionScore(
        id="Q1",
        expect="reports",
        no_answer_result=False,
        cited_slugs=["slug-a", "slug-z"],
        retrieved_slugs=["slug-a", "slug-z"],
        expected_cited=["slug-a"],
        expected_retrieved=["slug-a"],
        passed=True,
        sentences=[sscore(0, True, "supported"), sscore(1, True, "partly"), sscore(2, False)],
        cost_usd=0.02,
        stop_reason="end_turn",
        expected_reports=["slug-a", "slug-b"],
    )
    # Q2: report question answered "no reliable answer": a miss and a false no-answer.
    # One expected report, retrieved but not cited.
    q2 = QuestionScore(
        id="Q2",
        expect="reports",
        no_answer_result=True,
        cited_slugs=[],
        retrieved_slugs=["slug-c"],
        expected_cited=[],
        expected_retrieved=["slug-c"],
        passed=False,
        sentences=[sscore(0, False, no_answer=True)],
        cost_usd=0.01,
        stop_reason="end_turn",
        expected_reports=["slug-c"],
    )
    # Q3: no-answer question that cited a report: failed.
    # Sentences: not_supported, unjudged, supported.
    q3 = QuestionScore(
        id="Q3",
        expect="no_answer",
        no_answer_result=False,
        cited_slugs=["slug-d"],
        retrieved_slugs=["slug-d"],
        expected_cited=[],
        expected_retrieved=[],
        passed=False,
        sentences=[
            sscore(0, True, "not_supported"),
            sscore(1, True, "unjudged"),
            sscore(2, True, "supported"),
        ],
        cost_usd=0.03,
        stop_reason="end_turn",
        expected_reports=[],
    )
    run = totals([q1, q2, q3])
    # Fact sentences: 3 (Q1) + 0 (Q2) + 3 (Q3) = 6.
    # Unsupported: uncited 1 + partly 1 + not_supported 1 = 3. Unjudged 1.
    # Share: 3 / (6 - 1) = 0.6.
    assert run == RunTotals(
        fact_sentences=6,
        uncited=1,
        partly=1,
        not_supported=1,
        unjudged=1,
        unsupported=3,
        unsupported_share=pytest.approx(0.6),
        report_questions=2,
        report_questions_passed=1,
        expected_reports=3,
        expected_reports_retrieved=2,
        no_answer_questions=1,
        no_answer_questions_passed=0,
        false_no_answers=1,
        answer_cost_usd=pytest.approx(0.06),
    )


def test_totals_of_nothing_is_zero():
    run = totals([])
    assert run.fact_sentences == 0
    assert run.unsupported_share == 0.0
    assert run.answer_cost_usd == 0.0


def test_answer_from_saved_keeps_the_publisher_of_each_report_searched():
    ntsb = {**searched("Report N", f"{PDF_B}#page=5", "ntsb-slug-n", 5), "source_id": "ntsb"}
    result = answer_from_saved(
        saved(
            [sentence(NO_ANSWER)],
            searched_=[searched("Report A", f"{PDF_A}#page=3", "slug-a", 3), ntsb],
        )
    )
    # An entry saved before publishers were recorded reads as MAIB.
    assert [c.source_id for c in result.chunks] == ["maib", "ntsb"]
