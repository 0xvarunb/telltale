"""The eval question file: its shape, loading, checks, and the working or blind selection.

The file is JSON, version 1:

    {"version": 1, "questions": [
      {"id": "Q1", "question": "...", "expect": "reports", "expected_reports": ["slug-a"],
       "related_reports": [], "blind": false},
      ...
    ]}

A "reports" question expects at least one report; a "no_answer" question expects none and may
name related reports that can be cited only as related cases. Blind questions run only in a
final run.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

VERSION = 1
EXPECTS = ("reports", "no_answer")
FIELDS = ("id", "question", "expect", "expected_reports", "related_reports", "blind")


class QuestionFileError(Exception):
    """Raised when the question file is malformed or names something that does not exist."""


class BlindQuestionError(Exception):
    """Raised when a blind question would run outside a final run."""


@dataclass(frozen=True)
class EvalQuestion:
    id: str
    question: str
    expect: Literal["reports", "no_answer"]
    expected_reports: tuple[str, ...]
    related_reports: tuple[str, ...]
    blind: bool


def load_questions(path: Path, known_slugs: set[str]) -> list[EvalQuestion]:
    """Read and check the question file. Every problem with a slug is reported at once."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise QuestionFileError(f"Cannot read the question file {path}: {err}") from err

    if not isinstance(data, dict) or data.get("version") != VERSION:
        found = data.get("version") if isinstance(data, dict) else None
        raise QuestionFileError(f"Question file {path} has version {found!r}; expected {VERSION}.")
    rows = data.get("questions")
    if not isinstance(rows, list):
        raise QuestionFileError(f"Question file {path} has no list of questions.")

    questions = [_question(row, n) for n, row in enumerate(rows, start=1)]

    seen: set[str] = set()
    for question in questions:
        if question.id in seen:
            raise QuestionFileError(f"Question id {question.id} appears more than once.")
        seen.add(question.id)

    bad = [
        f"{question.id}: {slug}"
        for question in questions
        for slug in (*question.expected_reports, *question.related_reports)
        if slug not in known_slugs
    ]
    if bad:
        raise QuestionFileError(
            "These report slugs are not in the store:\n  " + "\n  ".join(bad)
        )
    return questions


def _question(row: object, n: int) -> EvalQuestion:
    if not isinstance(row, dict):
        raise QuestionFileError(f"Question {n} is not an object.")
    label = row.get("id", f"number {n}")
    missing = [field for field in FIELDS if field not in row]
    if missing:
        raise QuestionFileError(f"Question {label} is missing {', '.join(missing)}.")

    id_, text, expect, blind = row["id"], row["question"], row["expect"], row["blind"]
    if not isinstance(id_, str) or not id_:
        raise QuestionFileError(f"Question {label} needs a non-empty string id.")
    if not isinstance(text, str) or not text.strip():
        raise QuestionFileError(f"Question {id_} has no question text.")
    if expect not in EXPECTS:
        raise QuestionFileError(
            f"Question {id_} has expect {expect!r}; expected one of {', '.join(EXPECTS)}."
        )
    if not isinstance(blind, bool):
        raise QuestionFileError(f"Question {id_} needs blind to be true or false.")
    expected = _slugs(row["expected_reports"], id_, "expected_reports")
    related = _slugs(row["related_reports"], id_, "related_reports")

    if expect == "reports" and not expected:
        raise QuestionFileError(f"Question {id_} expects reports but names none.")
    if expect == "no_answer" and expected:
        raise QuestionFileError(
            f"Question {id_} expects no answer but names expected reports; "
            "put them in related_reports."
        )
    return EvalQuestion(
        id=id_,
        question=text,
        expect=expect,
        expected_reports=expected,
        related_reports=related,
        blind=blind,
    )


def _slugs(value: object, id_: str, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(s, str) and s for s in value):
        raise QuestionFileError(f"Question {id_} needs {field} to be a list of slugs.")
    return tuple(value)


def select(
    questions: list[EvalQuestion], *, final: bool, only: set[str] | None = None
) -> list[EvalQuestion]:
    """The questions a run asks: the working set, or with final=True the blind set.

    `only` narrows that set to the ids named. Naming a blind question without final refuses,
    so a blind question never runs by accident.
    """
    if only is not None:
        by_id = {question.id: question for question in questions}
        unknown = sorted(only - by_id.keys())
        if unknown:
            raise QuestionFileError(f"No question with id {', '.join(unknown)} in the file.")
        if not final:
            blind = sorted(id_ for id_ in only if by_id[id_].blind)
            if blind:
                raise BlindQuestionError(
                    f"{', '.join(blind)} is a blind question; it runs only with --final."
                )
        else:
            working = sorted(id_ for id_ in only if not by_id[id_].blind)
            if working:
                raise QuestionFileError(
                    f"{', '.join(working)} is a working question; --final runs only blind ones."
                )
    return [
        question
        for question in questions
        if question.blind == final and (only is None or question.id in only)
    ]
