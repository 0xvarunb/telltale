"""The run report and the judge review sheet, rebuilt from a run directory's saved files.

`write_report` reads `run.json`, `answers/<id>.json` and `judge/<id>.json` (Task 4's layout),
scores each answer and writes two files beside them:

- `report.md`: the headline share of sentences not backed by their cited passage, reports
  found, "no reliable answer" results, cost, and one row per question. It shows ids only,
  never a question's text, so a blind question's text cannot leak through it.
- `judge-review.csv`: a sample of judged sentences from working questions, for Varun to check
  the judge against his own verdict. Blind questions are never in it. It is written with a
  UTF-8 byte-order mark so a spreadsheet opens it as UTF-8.

It reads files only, so a report can be rebuilt at no cost and with no key.
"""

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path

from telltale.answer.brief import SEARCH_K
from telltale.evals.questions import EvalQuestion
from telltale.evals.run import ANSWERS_DIR, JUDGE_DIR, RUN_FILE
from telltale.evals.score import QuestionScore, RunTotals, Verdict, score_question, totals

REPORT_FILE = "report.md"
REVIEW_FILE = "judge-review.csv"
REVIEW_COLUMNS = (
    "id",
    "sentence_index",
    "sentence",
    "cited_passages",
    "verdict",
    "reason",
    "varun_verdict",
    "varun_note",
)
REVIEW_SIZE = 30
REVIEW_FLAGGED_MAX = 15  # partly and not_supported rows; the rest are supported
REVIEW_SEED = 20261119
FLAGGED = ("partly", "not_supported")


class ReportError(Exception):
    """Raised when a run directory cannot be reported on."""


@dataclass
class _Saved:
    """One answered question: its saved answer, its score and the judge's reasons by index."""

    question: EvalQuestion
    answer: dict
    score: QuestionScore
    reasons: dict[int, str]


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReportError(f"Cannot read {path}: {error}") from error


def _load(run_dir: Path, questions: list[EvalQuestion]) -> tuple[dict, list[EvalQuestion], dict]:
    """The run's metadata, its selected questions in order, and the answered ones by id."""
    run_file = run_dir / RUN_FILE
    if not run_file.is_file():
        raise ReportError(f"No {RUN_FILE} in {run_dir}; give a folder made by `telltale eval`.")
    meta = _read_json(run_file)
    by_id = {question.id: question for question in questions}
    missing = [id_ for id_ in meta["question_ids"] if id_ not in by_id]
    if missing:
        raise ReportError(
            f"The run asked {', '.join(missing)}, which the question file no longer has."
        )
    selected = [by_id[id_] for id_ in meta["question_ids"]]

    answered: dict[str, _Saved] = {}
    for question in selected:
        answer_file = run_dir / ANSWERS_DIR / f"{question.id}.json"
        if not answer_file.is_file():
            continue
        answer = _read_json(answer_file)
        judge_file = run_dir / JUDGE_DIR / f"{question.id}.json"
        records = _read_json(judge_file) if judge_file.is_file() else []
        verdicts: dict[int, Verdict] = {r["index"]: r["verdict"] for r in records}
        reasons = {r["index"]: r.get("reason", "") for r in records}
        answered[question.id] = _Saved(
            question=question,
            answer=answer,
            score=score_question(question, answer, verdicts),
            reasons=reasons,
        )
    return meta, selected, answered


def write_report(run_dir: Path, questions: list[EvalQuestion]) -> Path:
    """Write `report.md` and `judge-review.csv` in `run_dir`; return the report's path.

    `questions` must hold every question the run selected (more is fine). No model is called.
    """
    run_dir = Path(run_dir)
    meta, selected, answered = _load(run_dir, questions)
    report = run_dir / REPORT_FILE
    report.write_text(_markdown(run_dir, meta, selected, answered), encoding="utf-8")
    _write_review(run_dir / REVIEW_FILE, [answered[q.id] for q in selected if q.id in answered])
    return report


def run_totals(run_dir: Path, questions: list[EvalQuestion]) -> RunTotals:
    """The scored totals of a run, read from its saved files as `write_report` reads them."""
    _meta, selected, answered = _load(Path(run_dir), questions)
    return totals([answered[q.id].score for q in selected if q.id in answered])


# --- report.md ------------------------------------------------------------------------------


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _money(value: float) -> str:
    return f"${value:.4f}"


def _cell(text: str) -> str:
    """Text made safe for one markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _of(part: int, whole: int) -> str:
    return f"{part} of {whole}"


def _error_text(question: EvalQuestion, error: str) -> str:
    """A working question's error in full ("Type: message"); for a blind question only the
    type, since a message could quote the question."""
    if question.blind:
        error = error.split(":", 1)[0]
    return _cell(error)


# Why a run stopped early, by run.json's `stop_reason`.
_STOP_TEXT = {
    "cost_cap": "the cost cap was reached",
    "batch_estimate": "the estimate for the next batch would pass the cost cap, so it was "
    "not sent",
    "api_refused": "the API refused a request",
    "batch_lost": "a sent batch could no longer be reached",
}


def _stop_text(meta: dict) -> str:
    # A run.json written before `stop_reason` was kept could only stop at the cost cap.
    return _STOP_TEXT.get(meta.get("stop_reason") or "cost_cap", "reason not recorded")


def _not_answered(id_: str, meta: dict, errors: dict[str, str]) -> str:
    if id_ in errors:
        return "failed"
    if id_ not in meta["asked_ids"]:
        return "not asked"
    return "no answer saved"


def _markdown(
    run_dir: Path, meta: dict, selected: list[EvalQuestion], answered: dict[str, _Saved]
) -> str:
    scores = [answered[q.id].score for q in selected if q.id in answered]
    run = totals(scores)
    judged = run.fact_sentences - run.unjudged
    errors = {e["id"]: e["error"] for e in meta["errors"]}
    answer_cost, judge_cost = meta["answer_cost_usd"], meta["judge_cost_usd"]
    share = f"{run.unsupported_share:.1%}" if judged else "nothing judged"
    breakdown = (
        f"uncited {run.uncited}, partly {run.partly}, not supported {run.not_supported}, "
        f"unjudged {run.unjudged}"
    )
    if run.headline_valid:
        headline = (
            f"**Sentences not backed by their cited passage: {_of(run.unsupported, judged)} "
            f"({share})**: {breakdown} (left out of the {judged})."
        )
    else:
        headline = (
            f"**Headline not valid: {_of(run.unjudged, run.cited)} cited sentences "
            f"unjudged.** Sentences not backed by their cited passage: "
            f"{_of(run.unsupported, judged)} judged; {breakdown}."
        )

    lines = [
        f"# Eval run {run_dir.name}",
        "",
        f"- Started: {meta['started']}",
        f"- Commit: {meta['commit']}" + (" (with uncommitted changes)" if meta["dirty"] else ""),
        f"- Answering model: {meta['model']}",
        f"- Judge model: {meta['judge_model']}",
        f"- Final run: {_yes(meta['final'])}",
        f"- Stopped early: {_yes(meta['stopped_early'])}"
        + (f" ({_stop_text(meta)})" if meta["stopped_early"] else ""),
        f"- Questions answered: {_of(len(scores), len(selected))}",
        "",
        "## Headline",
        "",
        headline,
        "",
        "## Reports found",
        "",
        f"- Report questions passed: {_of(run.report_questions_passed, run.report_questions)}",
        f"- Expected reports retrieved in the top {SEARCH_K}: "
        f"{_of(run.expected_reports_retrieved, run.expected_reports)}",
        "",
        "## No reliable answer",
        "",
        "- No-answer questions passed: "
        f"{_of(run.no_answer_questions_passed, run.no_answer_questions)}",
        f'- Report questions wrongly answered "no reliable answer": {run.false_no_answers}',
        "",
        "## Cost",
        "",
        f"- Answers: {_money(answer_cost)}",
        f"- Judge: {_money(judge_cost)}",
        f"- Total: {_money(answer_cost + judge_cost)}",
        "- Per question answered: "
        + (_money((answer_cost + judge_cost) / len(scores)) if scores else "none answered"),
        "",
        "## Questions",
        "",
        "| id | expect | passed | expected cited | expected retrieved | other reports cited "
        "| sentences | unsupported | answer cost (USD, judging not included) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for question in selected:
        lines.append(_row(question, answered.get(question.id), meta, errors))

    problems = [
        f"- {q.id}: {_error_text(q, errors[q.id])}" for q in selected if q.id in errors
    ]
    if problems:
        lines += ["", "## Errors", "", *problems]
    return "\n".join(lines) + "\n"


def _row(question: EvalQuestion, saved: _Saved | None, meta: dict, errors: dict[str, str]) -> str:
    if saved is None:
        status = _not_answered(question.id, meta, errors)
        cells = [question.id, question.expect, status, "-", "-", "-", "-", "-", "-"]
        return "| " + " | ".join(cells) + " |"
    score = saved.score
    expected = len(score.expected_reports)
    others = [slug for slug in score.cited_slugs if slug not in score.expected_reports]
    own = totals([score])
    cells = [
        question.id,
        question.expect,
        _yes(score.passed),
        _of(len(score.expected_cited), expected) if expected else "-",
        _of(len(score.expected_retrieved), expected) if expected else "-",
        ", ".join(others) or "none",
        str(len(score.sentences)),
        f"{own.unsupported}; {own.unjudged} unjudged" if own.unjudged else str(own.unsupported),
        f"{score.cost_usd:.4f}",
    ]
    return "| " + " | ".join(_cell(cell) for cell in cells) + " |"


# --- judge-review.csv -----------------------------------------------------------------------


def _sample(working: list[_Saved]) -> list[tuple[_Saved, int]]:
    """Up to REVIEW_SIZE judged sentences: every partly and not_supported one up to
    REVIEW_FLAGGED_MAX, the rest supported. When there are too few supported sentences, more
    flagged ones fill the sheet. Drawn with a fixed seed; returned in question and sentence
    order."""
    flagged, supported = [], []
    for n, saved in enumerate(working):
        for s in saved.score.sentences:
            if s.verdict in FLAGGED:
                flagged.append((n, s.index))
            elif s.verdict == "supported":
                supported.append((n, s.index))

    rng = random.Random(REVIEW_SEED)
    rng.shuffle(flagged)
    rng.shuffle(supported)
    take_flagged = min(len(flagged), REVIEW_FLAGGED_MAX)
    take_supported = min(len(supported), REVIEW_SIZE - take_flagged)
    take_flagged = min(len(flagged), REVIEW_SIZE - take_supported)
    chosen = sorted(flagged[:take_flagged] + supported[:take_supported])
    return [(working[n], index) for n, index in chosen]


def _write_review(path: Path, answered: list[_Saved]) -> None:
    working = [saved for saved in answered if not saved.question.blind]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(REVIEW_COLUMNS)
        for saved, index in _sample(working):
            raw = saved.answer["sentences"][index]
            passages = list(dict.fromkeys(c["cited_text"] for c in raw["citations"]))
            writer.writerow(
                [
                    saved.question.id,
                    index,
                    raw["text"],
                    "\n\n".join(passages),
                    saved.score.sentences[index].verdict,
                    saved.reasons.get(index, ""),
                    "",
                    "",
                ]
            )
