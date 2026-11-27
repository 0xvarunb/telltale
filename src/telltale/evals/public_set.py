"""The open test set: the working eval questions as a public markdown page.

Each question is shown word for word with the reports chosen for it, as titles linked to the
publisher's page for each report (for TSIB, the PDF itself). No report text goes on the page,
and a blind question is refused outright, so the held-back set can never be published by this
module.
"""

import re

from telltale.evals.questions import EvalQuestion
from telltale.models import ReportMeta

TITLE = "Telltale test set"
SHOULD_COME_UP = "Reports that should come up"
NO_COVER = "The right answer: the reports don't cover this job."
CLOSEST = "Closest case, a different job"
RELATED = "Related cases"


class PublicSetError(ValueError):
    """Raised when the page would show a blind question or a report the store does not have."""


def public_set_markdown(
    questions: list[EvalQuestion], reports: dict[str, ReportMeta], *, held_back: int
) -> str:
    """The page: what the set is, then one section per question in id order.

    `held_back` is how many blind questions the private set keeps back; only the count is
    shown.
    """
    blind = sorted((q.id for q in questions if q.blind), key=_id_order)
    if blind:
        raise PublicSetError(
            f"{', '.join(blind)} is a blind question; blind questions are never published."
        )
    missing = [
        f"{q.id}: {slug}"
        for q in questions
        for slug in (*q.expected_reports, *q.related_reports)
        if slug not in reports
    ]
    if missing:
        raise PublicSetError("These report slugs are not in the store: " + ", ".join(missing))

    lines = [f"# {TITLE}", "", *_opening(len(questions), held_back)]
    for q in sorted(questions, key=lambda q: _id_order(q.id)):
        quoted = [f"> {_escape(line)}" for line in q.question.splitlines()]
        lines += ["", f"## {q.id}", "", *quoted]
        if q.expect == "reports":
            lines += ["", f"{SHOULD_COME_UP}:", "", *_links(q.expected_reports, reports)]
            if q.related_reports:
                lines += ["", f"{RELATED}:", "", *_links(q.related_reports, reports)]
        else:
            lines += ["", NO_COVER]
            if q.related_reports:
                lines += ["", f"{CLOSEST}:", "", *_links(q.related_reports, reports)]
    return "\n".join(lines) + "\n"


def _opening(working: int, held_back: int) -> list[str]:
    total = working + held_back
    held = (
        f"The other {_count(held_back, 'question')} {'is' if held_back == 1 else 'are'} held "
        "back for one final run and never published, so the tool can't be tuned to "
        f"{'it' if held_back == 1 else 'them'}."
        if held_back
        else ""
    )
    return [
        f"This page lists the {_count(working, 'working question')} from Telltale's private test set "
        f"of {total}. Each is a job a ship's officer would ask about before starting it. {held}"
        .rstrip(),
        "",
        "For each question, the reports that should come up were chosen before Telltale was "
        "asked: by a plain keyword search of every stored report page, then reading the "
        "passages it found. Where the right answer is that the reports don't cover the job, "
        "the closest case is listed when there is one.",
        "",
        "Each report title links to the publisher's page for the report; a TSIB title links to "
        "the report's PDF, as TSIB publishes no page for each report.",
    ]


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _links(slugs: tuple[str, ...], reports: dict[str, ReportMeta]) -> list[str]:
    return [f"- [{_escape(reports[slug].title)}](<{reports[slug].page_url}>)" for slug in slugs]


# Characters that markdown could read as markup in a title or a question line. The backslash
# comes first, so the escapes added for the others are not escaped again.
_MARKUP = ("\\", "[", "]", "*", "_", "<", ">", "`")


def _escape(text: str) -> str:
    for char in _MARKUP:
        text = text.replace(char, "\\" + char)
    return text


def _id_order(id_: str) -> tuple:
    """Q2 before Q10: compare the digit runs as numbers."""
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in re.findall(r"\d+|\D+", id_)
    )
