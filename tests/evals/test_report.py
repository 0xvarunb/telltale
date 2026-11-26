"""The run report and the judge review sheet, rebuilt from a run directory's saved files.
Synthetic questions and answers only; no model is ever called."""

import csv
import json
from pathlib import Path

import pytest

from telltale.answer import NO_ANSWER
from telltale.evals.questions import EvalQuestion
from telltale.evals.report import (
    REPORT_FILE,
    REVIEW_COLUMNS,
    REVIEW_FILE,
    ReportError,
    write_report,
)

PDF_A = "https://assets.example/synthetic-a.pdf"
PDF_B = "https://assets.example/synthetic-b.pdf"
SEARCHED = [
    {"report_title": "Report A", "page": 3, "source": f"{PDF_A}#page=3",
     "report_slug": "synthetic-a"},
    {"report_title": "Report B", "page": 7, "source": f"{PDF_B}#page=7",
     "report_slug": "synthetic-b"},
]


def question(
    id_: str, expect: str = "reports", blind: bool = False, text: str | None = None
) -> EvalQuestion:
    return EvalQuestion(
        id=id_,
        question=text or f"Synthetic question {id_}?",
        expect=expect,  # type: ignore[arg-type]
        expected_reports=("synthetic-a",) if expect == "reports" else (),
        related_reports=(),
        blind=blind,
    )


def cite(source: str = f"{PDF_A}#page=3", title: str = "Report A", text: str = "A passage.") -> dict:
    return {"report_title": title, "source": source, "cited_text": text}


def sentence(text: str, *citations: dict) -> dict:
    return {"text": text, "citations": list(citations)}


def saved(q: EvalQuestion, sentences: list[dict], cost: float = 0.01) -> dict:
    return {
        "question": q.question,
        "sentences": sentences,
        "input_tokens": 100,
        "output_tokens": 20,
        "cost_usd": cost,
        "stop_reason": "end_turn",
        "searched": SEARCHED,
    }


def make_run(
    tmp_path: Path,
    answers: dict[str, tuple[dict, dict[int, str]]],
    *,
    question_ids: list[str] | None = None,
    asked_ids: list[str] | None = None,
    errors: list[dict] | None = None,
    final: bool = False,
    stopped_early: bool = False,
    judge_cost: float = 0.004,
    stop_reason: str | None = None,
) -> Path:
    """A run directory as `run_eval` leaves it: `answers` maps an id to its saved answer and
    its verdicts by sentence index, written as judge records."""
    run_dir = tmp_path / "run"
    (run_dir / "answers").mkdir(parents=True)
    (run_dir / "judge").mkdir()
    for id_, (answer, verdicts) in answers.items():
        (run_dir / "answers" / f"{id_}.json").write_text(json.dumps(answer), encoding="utf-8")
        records = [
            {"index": i, "verdict": v, "reason": f"Reason for {id_} sentence {i}.",
             "input_tokens": 50, "output_tokens": 10}
            for i, v in verdicts.items()
        ]
        (run_dir / "judge" / f"{id_}.json").write_text(json.dumps(records), encoding="utf-8")
    ids = question_ids or list(answers)
    meta = {
        "started": "2026-11-19T10:00:00+05:30",
        "commit": "abc1234",
        "dirty": False,
        "model": "claude-test-answer",
        "judge_model": "claude-test-judge",
        "final": final,
        "question_ids": ids,
        "asked_ids": asked_ids if asked_ids is not None else ids,
        "answer_cost_usd": sum(a["cost_usd"] for a, _ in answers.values()),
        "judge_cost_usd": judge_cost,
        "stopped_early": stopped_early,
        "errors": errors or [],
    }
    if stop_reason is not None:
        meta["stop_reason"] = stop_reason
    (run_dir / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    return run_dir


def headline_run(tmp_path: Path, **kw) -> tuple[Path, list[EvalQuestion]]:
    """W1 has one supported, one partly, one uncited, one not supported and one unjudged fact
    sentence; W2 is a correct no-answer."""
    w1, w2 = question("W1"), question("W2", expect="no_answer")
    answers = {
        "W1": (
            saved(w1, [
                sentence("Fact one.", cite()),
                sentence("Fact two.", cite()),
                sentence("Fact three."),
                sentence("Fact four.", cite()),
                sentence("Fact five.", cite()),
            ], cost=0.02),
            {0: "supported", 1: "partly", 3: "not_supported"},
        ),
        "W2": (saved(w2, [sentence(NO_ANSWER)], cost=0.01), {}),
    }
    return make_run(tmp_path, answers, **kw), [w1, w2]


def test_headline_counts(tmp_path: Path):
    run_dir, questions = headline_run(tmp_path)

    path = write_report(run_dir, questions)

    assert path == run_dir / REPORT_FILE
    text = path.read_text(encoding="utf-8")
    # Five fact sentences, four cited, one of those unjudged: more than 5% of the cited
    # sentences are unjudged, so no share is given, only the counts.
    assert "Headline not valid: 1 of 4 cited sentences unjudged" in text
    assert "Sentences not backed by their cited passage: 3 of 4 judged" in text
    assert "75.0%" not in text
    assert "uncited 1, partly 1, not supported 1, unjudged 1" in text
    assert "Report questions passed: 1 of 1" in text
    assert "Expected reports retrieved in the top 8: 1 of 1" in text
    assert "No-answer questions passed: 1 of 1" in text
    assert 'Report questions wrongly answered "no reliable answer": 0' in text


def twenty_cited(tmp_path: Path, unjudged: int) -> tuple[Path, list[EvalQuestion]]:
    """One question with 20 cited fact sentences: `unjudged` of them with no verdict, two not
    supported, the rest supported."""
    w1 = question("W1")
    sentences = [sentence(f"Fact {n}.", cite()) for n in range(20)]
    verdicts = {n: "supported" for n in range(unjudged, 20)}
    verdicts[18], verdicts[19] = "not_supported", "not_supported"
    return make_run(tmp_path, {"W1": (saved(w1, sentences), verdicts)}), [w1]


def test_headline_stands_with_exactly_five_percent_unjudged(tmp_path: Path):
    run_dir, questions = twenty_cited(tmp_path, unjudged=1)

    text = write_report(run_dir, questions).read_text(encoding="utf-8")

    assert "Headline not valid" not in text
    # 1 of 20 is exactly 5%: the share stands, over the 19 judged.
    assert "Sentences not backed by their cited passage: 2 of 19 (10.5%)" in text
    assert "unjudged 1" in text


def test_headline_not_valid_above_five_percent_unjudged(tmp_path: Path):
    run_dir, questions = twenty_cited(tmp_path, unjudged=2)

    text = write_report(run_dir, questions).read_text(encoding="utf-8")

    assert "Headline not valid: 2 of 20 cited sentences unjudged" in text
    assert "Sentences not backed by their cited passage: 2 of 18 judged" in text
    assert "%" not in text.split("## Reports found")[0].split("## Headline")[1]


@pytest.mark.parametrize(
    "reason,said",
    [
        ("cost_cap", "the cost cap was reached"),
        ("batch_estimate", "the estimate for the next batch would pass the cost cap"),
        ("api_refused", "the API refused a request"),
        ("batch_lost", "a sent batch could no longer be reached"),
        (None, "the cost cap was reached"),  # a run.json from before stop_reason was kept
    ],
)
def test_report_says_why_the_run_stopped_early(tmp_path: Path, reason, said):
    w1 = question("W1")
    run_dir = make_run(tmp_path, {"W1": (saved(w1, [sentence("Fact.", cite())]), {0: "supported"})},
                       stopped_early=True, stop_reason=reason)

    text = write_report(run_dir, [w1]).read_text(encoding="utf-8")

    assert f"Stopped early: yes ({said}" in text


def test_report_names_the_run_and_keeps_the_section_order(tmp_path: Path):
    run_dir, questions = headline_run(tmp_path, stopped_early=True)

    text = write_report(run_dir, questions).read_text(encoding="utf-8")

    for line in (
        "Commit: abc1234",
        "Answering model: claude-test-answer",
        "Judge model: claude-test-judge",
        "Final run: no",
        "Stopped early: yes",
    ):
        assert line in text
    order = [
        "Sentences not backed by their cited passage",
        "## Reports found",
        "## No reliable answer",
        "## Cost",
        "## Questions",
    ]
    positions = [text.index(heading) for heading in order]
    assert positions == sorted(positions)
    assert "Answers: $0.0300" in text
    assert "Judge: $0.0040" in text
    assert "Per question answered: $0.0170" in text


def test_question_table_has_one_row_per_question(tmp_path: Path):
    run_dir, questions = headline_run(tmp_path)

    text = write_report(run_dir, questions).read_text(encoding="utf-8")

    assert "answer cost (USD, judging not included)" in text
    w1 = next(line for line in text.splitlines() if line.startswith("| W1 |"))
    cells = [cell.strip() for cell in w1.strip("|").split("|")]
    # id, expect, passed, expected cited, expected retrieved, other cited, sentences,
    # unsupported, answer cost
    assert cells == [
        "W1", "reports", "yes", "1 of 1", "1 of 1", "none", "5", "3; 1 unjudged", "0.0200"
    ]
    w2 = next(line for line in text.splitlines() if line.startswith("| W2 |"))
    assert [cell.strip() for cell in w2.strip("|").split("|")][:3] == ["W2", "no_answer", "yes"]


def test_a_question_with_no_answer_file_shows_whether_it_failed_or_was_not_asked(
    tmp_path: Path,
):
    w1, w2, w3 = question("W1"), question("W2"), question("W3")
    run_dir = make_run(
        tmp_path,
        {"W1": (saved(w1, [sentence("Fact.", cite())]), {0: "supported"})},
        question_ids=["W1", "W2", "W3"],
        asked_ids=["W1", "W2"],
        errors=[{"id": "W2", "error": "RuntimeError: synthetic failure"}],
        stopped_early=True,
    )

    text = write_report(run_dir, [w1, w2, w3]).read_text(encoding="utf-8")

    rows = {line.split("|")[1].strip(): line for line in text.splitlines() if line.startswith("| W")}
    assert "| failed |" in rows["W2"]
    assert "| not asked |" in rows["W3"]
    assert "RuntimeError: synthetic failure" in text
    assert "Questions answered: 1 of 3" in text


def test_blind_question_text_never_appears(tmp_path: Path):
    secret = "Synthetic blind question that must stay private?"
    other = "Second synthetic blind question, also private?"
    b1 = question("B1", blind=True, text=secret)
    b2 = question("B2", blind=True, text=other)
    run_dir = make_run(
        tmp_path,
        {"B1": (saved(b1, [sentence("Fact.", cite())]), {0: "partly"})},
        question_ids=["B1", "B2"],
        errors=[{"id": "B2", "error": f"RuntimeError: could not ask {other!r}"}],
        final=True,
    )

    write_report(run_dir, [b1, b2])

    report = (run_dir / REPORT_FILE).read_text(encoding="utf-8")
    sheet = (run_dir / REVIEW_FILE).read_text(encoding="utf-8-sig")
    assert "B1" in report and "B2" in report
    assert "Final run: yes" in report
    for text in (secret, other):
        assert text not in report
        assert text not in sheet


def test_a_blind_error_shows_only_its_type_and_a_working_error_its_message(tmp_path: Path):
    secret = "Synthetic blind question quoted in an error?"
    b1 = question("B1", blind=True, text=secret)
    w1 = question("W1")
    run_dir = make_run(
        tmp_path,
        {},
        question_ids=["B1", "W1"],
        errors=[
            {"id": "B1", "error": f"APIError: the request for {secret} was refused"},
            {"id": "W1", "error": "RuntimeError: synthetic working failure"},
        ],
    )

    report = write_report(run_dir, [b1, w1]).read_text(encoding="utf-8")

    assert "- B1: APIError\n" in report
    assert secret not in report
    assert "refused" not in report
    assert "- W1: RuntimeError: synthetic working failure" in report


def judged_run(tmp_path: Path, plan: dict[str, list[str | None]], blind: set[str] = frozenset()):
    """Questions whose sentences carry the verdicts in `plan` (None for an uncited sentence)."""
    questions, answers = [], {}
    for id_, verdicts in plan.items():
        q = question(id_, blind=id_ in blind)
        questions.append(q)
        sentences = [
            sentence(f"{id_} fact {i}.", *([cite()] if v is not None else []))
            for i, v in enumerate(verdicts)
        ]
        answers[id_] = (
            saved(q, sentences),
            {i: v for i, v in enumerate(verdicts) if v is not None},
        )
    return make_run(tmp_path, answers), questions


def read_sheet(run_dir: Path) -> list[dict]:
    with (run_dir / REVIEW_FILE).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == list(REVIEW_COLUMNS)
        return list(reader)


def test_review_sheet_has_30_rows_spread_across_verdicts(tmp_path: Path):
    plan = {
        "W1": ["partly"] * 12 + ["supported"] * 20 + ["unjudged", None],
        "W2": ["not_supported"] * 8 + ["supported"] * 20,
    }
    run_dir, questions = judged_run(tmp_path, plan)

    write_report(run_dir, questions)
    rows = read_sheet(run_dir)

    assert len(rows) == 30
    # A byte-order mark, so a spreadsheet reads the file as UTF-8.
    assert (run_dir / REVIEW_FILE).read_bytes().startswith(b"\xef\xbb\xbf")
    verdicts = [row["verdict"] for row in rows]
    assert sum(v in ("partly", "not_supported") for v in verdicts) == 15
    assert verdicts.count("supported") == 15
    assert "unjudged" not in verdicts
    assert all(row["varun_verdict"] == "" and row["varun_note"] == "" for row in rows)
    first = rows[0]
    assert first["reason"] == f"Reason for {first['id']} sentence {first['sentence_index']}."
    assert first["sentence"] == f"{first['id']} fact {first['sentence_index']}."
    # Each passage under its report title, as the judge saw it.
    assert first["cited_passages"] == "Report title: Report A\nA passage."

    # The sample is drawn with a fixed seed, so a rebuild gives the same sheet.
    before = (run_dir / REVIEW_FILE).read_bytes()
    write_report(run_dir, questions)
    assert (run_dir / REVIEW_FILE).read_bytes() == before


def test_review_sheet_takes_every_flagged_sentence_when_there_are_few(tmp_path: Path):
    plan = {"W1": ["partly", "not_supported", "partly"] + ["supported"] * 40}
    run_dir, questions = judged_run(tmp_path, plan)

    write_report(run_dir, questions)
    verdicts = [row["verdict"] for row in read_sheet(run_dir)]

    assert len(verdicts) == 30
    assert verdicts.count("supported") == 27


def test_review_sheet_excludes_blind_questions(tmp_path: Path):
    plan = {"W1": ["supported", "partly"], "B1": ["not_supported", "supported"]}
    run_dir, questions = judged_run(tmp_path, plan, blind={"B1"})

    write_report(run_dir, questions)
    rows = read_sheet(run_dir)

    assert {row["id"] for row in rows} == {"W1"}
    assert len(rows) == 2


def test_review_sheet_escapes_commas_quotes_and_newlines(tmp_path: Path):
    awkward = 'A "quoted", comma-laden\nsentence.'
    q = question("W1")
    run_dir = make_run(
        tmp_path,
        {"W1": (
            saved(q, [sentence(awkward, cite(text='Line one, "two"\nline three.'),
                               cite(source=f"{PDF_B}#page=7", title="Report B",
                                    text="Second passage."))]),
            {0: "supported"},
        )},
    )

    write_report(run_dir, [q])
    (row,) = read_sheet(run_dir)

    assert row["sentence"] == awkward
    assert row["cited_passages"] == (
        'Report title: Report A\nLine one, "two"\nline three.\n\n'
        "Report title: Report B\nSecond passage."
    )


def test_report_rebuilds_from_saved_files_without_a_client(tmp_path: Path, monkeypatch):
    import anthropic

    import telltale.evals.judge as judge

    def no_model(*args, **kwargs):
        raise AssertionError("the report must not call a model")

    monkeypatch.setattr(anthropic, "Anthropic", no_model)
    monkeypatch.setattr(judge, "judge_answer", no_model)
    run_dir, questions = headline_run(tmp_path)

    first = write_report(run_dir, questions).read_text(encoding="utf-8")
    (run_dir / REPORT_FILE).unlink()
    (run_dir / REVIEW_FILE).unlink()
    second = write_report(run_dir, questions).read_text(encoding="utf-8")

    assert first == second
    assert (run_dir / REVIEW_FILE).is_file()


def test_a_run_id_missing_from_the_question_file_is_refused(tmp_path: Path):
    run_dir, questions = headline_run(tmp_path)

    with pytest.raises(ReportError, match="W2"):
        write_report(run_dir, questions[:1])


def test_a_directory_with_no_run_file_is_refused(tmp_path: Path):
    with pytest.raises(ReportError, match="run.json"):
        write_report(tmp_path, [])
