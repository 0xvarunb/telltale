"""The open test set page: the working questions with their chosen reports, titles and links only.
Synthetic questions and reports only."""

import pytest

from telltale.evals.public_set import PublicSetError, public_set_markdown
from telltale.evals.questions import EvalQuestion
from telltale.models import ReportMeta


def meta(slug: str, title: str) -> ReportMeta:
    return ReportMeta(
        slug=slug,
        title=title,
        page_url=f"https://www.gov.uk/maib-reports/{slug}",
        published="2026-03-12T10:15:00.000+00:00",
        occurred=None,
        vessel_types=[],
        report_type="investigation-report",
        pdf_url=None,
    )


REPORTS = {
    "synthetic-a": meta("synthetic-a", "Report A on a synthetic ship"),
    "synthetic-b": meta("synthetic-b", "Report B on another synthetic ship"),
}


def question(
    id_: str,
    text: str,
    expect: str = "reports",
    expected: tuple[str, ...] = (),
    related: tuple[str, ...] = (),
    blind: bool = False,
) -> EvalQuestion:
    return EvalQuestion(
        id=id_,
        question=text,
        expect=expect,  # type: ignore[arg-type]
        expected_reports=expected,
        related_reports=related,
        blind=blind,
    )


def test_public_set_lists_each_working_question_with_its_chosen_reports():
    questions = [
        question("Q10", "We are about to do synthetic job ten. What went wrong?",
                 expected=("synthetic-a", "synthetic-b")),
        question("Q2", "We are about to do synthetic job two. What went wrong?",
                 expect="no_answer", related=("synthetic-b",)),
    ]
    page = public_set_markdown(questions, REPORTS, held_back=17)

    assert "We are about to do synthetic job ten. What went wrong?" in page
    assert "We are about to do synthetic job two. What went wrong?" in page
    assert "Q10" in page and "Q2" in page
    # Id order, by number: Q2 before Q10.
    assert page.index("Q2") < page.index("Q10")

    q10 = page[page.index("Q10"):]
    assert "Reports that should come up" in q10
    assert "[Report A on a synthetic ship](<https://www.gov.uk/maib-reports/synthetic-a>)" in q10
    assert "[Report B on another synthetic ship](<https://www.gov.uk/maib-reports/synthetic-b>)" in q10

    q2 = page[page.index("Q2"):page.index("Q10")]
    assert "The right answer: the reports don't cover this job" in q2
    assert "Closest case, a different job" in q2
    assert "[Report B on another synthetic ship](<https://www.gov.uk/maib-reports/synthetic-b>)" in q2
    assert "Reports that should come up" not in q2

    # The opening says what the set is: 2 working of a private 19, 17 held back.
    assert "2 working questions" in page
    assert "19" in page and "17" in page
    assert "keyword search" in page
    assert "—" not in page  # no em dashes on a public page


def test_public_set_refuses_a_blind_question():
    questions = [
        question("W1", "Synthetic working question?", expected=("synthetic-a",)),
        question("B7", "Synthetic blind question?", expected=("synthetic-a",), blind=True),
    ]
    with pytest.raises(PublicSetError, match="B7"):
        public_set_markdown(questions, REPORTS, held_back=17)


def test_public_set_refuses_a_report_not_in_the_store():
    questions = [
        question("W1", "Synthetic working question?", expect="no_answer",
                 related=("synthetic-missing",)),
    ]
    with pytest.raises(PublicSetError, match="synthetic-missing"):
        public_set_markdown(questions, REPORTS, held_back=17)


@pytest.mark.parametrize("char", ["\\", "[", "]", "*", "_", "<", ">", "`"])
def test_public_set_escapes_markdown_in_titles_and_questions(char):
    reports = {"synthetic-a": meta("synthetic-a", f"Report {char}A{char} on a synthetic ship")}
    questions = [
        question("W1", f"Synthetic job {char}one{char}?", expected=("synthetic-a",)),
    ]

    page = public_set_markdown(questions, reports, held_back=0)

    escaped = "\\" + char
    assert (
        f"- [Report {escaped}A{escaped} on a synthetic ship]"
        "(<https://www.gov.uk/maib-reports/synthetic-a>)"
    ) in page
    assert f"> Synthetic job {escaped}one{escaped}?" in page


def test_public_set_escapes_a_backslash_before_the_characters_it_escapes():
    reports = {"synthetic-a": meta("synthetic-a", "Report \\[A] on a synthetic ship")}
    questions = [question("W1", "Synthetic job?", expected=("synthetic-a",))]

    page = public_set_markdown(questions, reports, held_back=0)

    # The backslash is escaped once, then the bracket: never a doubled escape of the bracket.
    assert "[Report \\\\\\[A\\] on a synthetic ship]" in page


def test_public_set_says_titles_link_to_the_publishers_page():
    questions = [question("W1", "Synthetic job?", expected=("synthetic-a",))]

    page = public_set_markdown(questions, REPORTS, held_back=0)

    # TSIB publishes no page for each report, so its titles link to the PDF, and the page says so.
    assert (
        "Each report title links to the publisher's page for the report; a TSIB title links to "
        "the report's PDF, as TSIB publishes no page for each report."
    ) in page
    assert "GOV.UK" not in page
