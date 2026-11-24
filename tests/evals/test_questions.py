import json
from pathlib import Path

import pytest

from telltale.evals.questions import (
    BlindQuestionError,
    EvalQuestion,
    QuestionFileError,
    load_questions,
    select,
)

KNOWN = {"slug-a", "slug-b", "slug-c"}


def q(
    id_: str,
    expect: str = "reports",
    expected: list[str] | None = None,
    related: list[str] | None = None,
    blind: bool = False,
) -> dict:
    if expected is None:
        expected = ["slug-a"] if expect == "reports" else []
    return {
        "id": id_,
        "question": f"Synthetic question {id_}?",
        "expect": expect,
        "expected_reports": expected,
        "related_reports": related or [],
        "blind": blind,
    }


def write(tmp_path: Path, questions: list[dict], version: int = 1) -> Path:
    path = tmp_path / "questions.json"
    path.write_text(json.dumps({"version": version, "questions": questions}))
    return path


def both_kinds(tmp_path: Path) -> list[EvalQuestion]:
    path = write(
        tmp_path,
        [
            q("Q1", expected=["slug-a"]),
            q("Q2", expect="no_answer", related=["slug-b"], blind=True),
        ],
    )
    return load_questions(path, KNOWN)


def test_loads_both_kinds(tmp_path):
    first, second = both_kinds(tmp_path)

    assert first == EvalQuestion(
        id="Q1",
        question="Synthetic question Q1?",
        expect="reports",
        expected_reports=("slug-a",),
        related_reports=(),
        blind=False,
    )
    assert isinstance(first.expected_reports, tuple)
    assert second.expect == "no_answer"
    assert second.expected_reports == ()
    assert second.related_reports == ("slug-b",)
    assert second.blind is True


def test_unknown_slug_lists_every_bad_slug(tmp_path):
    path = write(
        tmp_path,
        [
            q("Q1", expected=["slug-a", "no-such-one"]),
            q("Q2", expect="no_answer", related=["no-such-two"]),
        ],
    )

    with pytest.raises(QuestionFileError) as err:
        load_questions(path, KNOWN)

    message = str(err.value)
    assert "no-such-one" in message
    assert "no-such-two" in message
    assert "Q1" in message and "Q2" in message


def test_reports_question_needs_a_report(tmp_path):
    path = write(tmp_path, [q("Q1", expect="reports", expected=[])])

    with pytest.raises(QuestionFileError, match="Q1"):
        load_questions(path, KNOWN)


def test_no_answer_question_has_no_expected_reports(tmp_path):
    path = write(tmp_path, [q("Q1", expect="no_answer", expected=["slug-a"])])

    with pytest.raises(QuestionFileError, match="Q1"):
        load_questions(path, KNOWN)


def test_unknown_expect_rejected(tmp_path):
    path = write(tmp_path, [q("Q1", expect="maybe", expected=["slug-a"])])

    with pytest.raises(QuestionFileError, match="Q1"):
        load_questions(path, KNOWN)


def test_duplicate_ids_rejected(tmp_path):
    path = write(tmp_path, [q("Q1"), q("Q1", expected=["slug-b"])])

    with pytest.raises(QuestionFileError, match="Q1"):
        load_questions(path, KNOWN)


def test_unknown_version_rejected(tmp_path):
    path = write(tmp_path, [q("Q1")], version=2)

    with pytest.raises(QuestionFileError, match="version"):
        load_questions(path, KNOWN)


def test_missing_field_rejected(tmp_path):
    bad = q("Q1")
    del bad["blind"]
    path = write(tmp_path, [bad])

    with pytest.raises(QuestionFileError, match="blind"):
        load_questions(path, KNOWN)


def test_select_working_excludes_blind(tmp_path):
    questions = both_kinds(tmp_path)

    assert [x.id for x in select(questions, final=False)] == ["Q1"]


def test_select_final_returns_blind_only(tmp_path):
    questions = both_kinds(tmp_path)

    assert [x.id for x in select(questions, final=True)] == ["Q2"]


def test_select_only_narrows_the_working_set(tmp_path):
    path = write(tmp_path, [q("Q1"), q("Q3", expected=["slug-c"]), q("Q2", blind=True)])
    questions = load_questions(path, KNOWN)

    assert [x.id for x in select(questions, final=False, only={"Q3"})] == ["Q3"]


def test_only_naming_a_blind_question_without_final_refuses(tmp_path):
    questions = both_kinds(tmp_path)

    with pytest.raises(BlindQuestionError) as err:
        select(questions, final=False, only={"Q2"})

    assert "Q2" in str(err.value)
    assert "--final" in str(err.value)


def test_only_unknown_id_refuses(tmp_path):
    questions = both_kinds(tmp_path)

    with pytest.raises(QuestionFileError, match="Q9"):
        select(questions, final=False, only={"Q9"})


def test_only_naming_a_working_question_with_final_refuses(tmp_path):
    questions = both_kinds(tmp_path)

    with pytest.raises(QuestionFileError, match="Q1"):
        select(questions, final=True, only={"Q1"})
