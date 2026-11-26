"""The eval run: ask each question once, judge the answer, and save both.

A run directory holds `answers/<id>.json` (the shape `telltale.card.to_json` writes),
`judge/<id>.json` (the judge's records for that answer) and `run.json` (what was run, on which
commit, which questions were asked, what it cost, and any question that failed). `run.json` is
rewritten after every question, and once more if the run is stopped, so it always says how far
the run got.

In batch mode the answers go as one Message Batch at half the live price, then every judge call
as a second one. Each batch is polled until it ends, and its id is kept in `run.json`, so a
batch that was sent is on record even if the run is stopped while waiting. Polling keeps going
through network and server errors. The files written are the same as a live run's, so the
report reads either.

An error on one question is recorded and the run goes on, unless the API refused the request
outright (a bad request, a bad key, no access, no such model): that would repeat for every
question, so the run stops there with `RunHalted`. In batch mode the run also stops when every
request in a batch errored, or any was refused as invalid.

A final run asks the blind questions. It refuses an uncommitted tree, so its results always
belong to a commit, and it is added to a log before its first question, so every time the blind
set is sent is on record, even when the run is stopped part way.
"""

import json
import re
import time

import anthropic
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from telltale import card
from telltale.answer.brief import SEARCH_K, answer_from_response, no_results_answer
from telltale.answer.prompt import build_request
from telltale.evals.judge import (
    is_retryable,
    judge_cost,
    judge_record,
    judge_requests,
    require_judge_model,
    unjudged_record,
)
from telltale.evals.questions import EvalQuestion
from telltale.evals.score import Verdict
from telltale.models import Answer, Chunk
from telltale.pricing import cost_usd, require_price

DEFAULT_FINAL_LOG = Path("data/eval/final-runs.jsonl")
RUN_FILE = "run.json"
ANSWERS_DIR = "answers"
JUDGE_DIR = "judge"

Judge = Callable[[dict], tuple[dict[int, Verdict], list[dict], float]]

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# How long batch mode waits between checks on a batch that has not ended.
DEFAULT_POLL_SECONDS = 60.0
# The batch estimate counts this many characters of a request as one input token.
CHARS_PER_TOKEN = 4
# How many checks on a sent batch in a row may fail with a network or server error before the
# run gives up waiting (at the default poll, half an hour).
MAX_WAIT_FAILURES = 30
# How long Anthropic keeps a batch's results after it is sent (batch processing docs).
RESULTS_KEPT_DAYS = 29

# Errors that mean the API refused the request itself; every question would get the same.
SETUP_ERRORS = (
    anthropic.BadRequestError,
    anthropic.AuthenticationError,
    anthropic.PermissionDeniedError,
    anthropic.NotFoundError,
)


class DirtyTreeError(Exception):
    """Raised when a final run is asked for on a tree with uncommitted changes."""


class BatchRequestFailed(Exception):
    """One request in a batch came back errored, canceled or expired, or not at all.

    `error_type` is the API's error type for an errored request (such as
    "invalid_request_error"), else None."""

    def __init__(self, message: str, error_type: str | None = None):
        super().__init__(message)
        self.error_type = error_type


class RunHalted(Exception):
    """The run stopped part way, for a reason that would repeat for every question (or a sent
    batch could no longer be reached). `run_dir` holds what was saved before it stopped."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason
        self.run_dir: Path | None = None


def _error_text(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def safe_name(text: str) -> str:
    """`text` with every run of characters unsafe in a file name replaced by one hyphen."""
    return _UNSAFE.sub("-", text).strip("-") or "unnamed"


def run_eval(
    questions: list[EvalQuestion],
    ask: Callable[[str], Answer] | None,
    judge: Judge | None,
    out_dir: Path,
    max_cost_usd: float,
    *,
    model: str,
    judge_model: str,
    final: bool,
    commit: str,
    dirty: bool,
    final_log: Path = DEFAULT_FINAL_LOG,
    on_start: Callable[[Path], None] | None = None,
    batch: bool = False,
    index=None,
    client=None,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    on_batch: Callable[[str, str], None] | None = None,
) -> Path:
    """Ask and judge each question in turn, saving as it goes; return the run directory.

    Before each question the cost so far (answers plus judging) is checked against
    `max_cost_usd`; once it is reached no further question is asked. An exception on one
    question is recorded in `run.json` and the run goes on, except an API refusal of the request
    itself, which is recorded and stops the run with `RunHalted`. A final run is added to
    `final_log` before its first question. `on_start` is told the run directory before the
    first question.

    With `batch`, `ask` and `judge` are not used: each question is searched in `index`, the
    answers are sent through `client` as one Message Batch and the judge calls as a second, both
    priced at the batch rate. A batch is sent only if the cost so far plus an estimate of its
    cost stays within `max_cost_usd`; otherwise the run stops early. `sleep(poll_seconds)`
    is called between checks on a batch, and `on_batch` is told ("answer" or "judge", batch id)
    once each batch is sent.
    """
    if batch:
        if index is None or client is None:
            raise ValueError("Batch mode needs the search index and a client.")
        require_price(model)
        require_judge_model(judge_model)
    if final and dirty:
        raise DirtyTreeError(
            "A final run needs every change committed, so its results belong to one commit."
        )
    unsafe = [question.id for question in questions if safe_name(question.id) != question.id]
    if unsafe:
        raise ValueError(
            f"Question id {', '.join(unsafe)} cannot be used as a file name; "
            "use letters, digits, dots, hyphens and underscores only."
        )

    started = datetime.now().astimezone()
    run_dir = _new_run_dir(out_dir, f"{started:%Y%m%d-%H%M}-{commit[:7]}-{model}")
    (run_dir / ANSWERS_DIR).mkdir()
    (run_dir / JUDGE_DIR).mkdir()
    meta = {
        "started": started.isoformat(timespec="seconds"),
        "commit": commit,
        "dirty": dirty,
        "model": model,
        "judge_model": judge_model,
        "final": final,
        "question_ids": [question.id for question in questions],
        "asked_ids": [],
        "answer_cost_usd": 0.0,
        "judge_cost_usd": 0.0,
        "stopped_early": False,
        "errors": [],
        "answer_batch_id": None,
        "judge_batch_id": None,
        # Why the run stopped early: "cost_cap", "batch_estimate", "api_refused" or
        # "batch_lost"; None when it did not.
        "stop_reason": None,
    }
    _write_meta(run_dir, meta)
    if final:
        log_final_run(run_dir, final_log)
    if on_start is not None:
        on_start(run_dir)

    try:
        if batch:
            _run_batches(
                questions, run_dir, meta, max_cost_usd, index=index, client=client, model=model,
                judge_model=judge_model, poll_seconds=poll_seconds, sleep=sleep,
                on_batch=on_batch,
            )
            return run_dir
        for question in questions:
            if meta["answer_cost_usd"] + meta["judge_cost_usd"] >= max_cost_usd:
                _stop(meta, "cost_cap")
                break
            # Counted as asked before the call: a question that fails or is interrupted may
            # still have been sent.
            meta["asked_ids"].append(question.id)
            try:
                result = ask(question.question)
                meta["answer_cost_usd"] += result.cost_usd
                text = card.to_json(result)
                (run_dir / ANSWERS_DIR / f"{question.id}.json").write_text(text, encoding="utf-8")
                _verdicts, records, judge_cost = judge(json.loads(text))
                meta["judge_cost_usd"] += judge_cost
                (run_dir / JUDGE_DIR / f"{question.id}.json").write_text(
                    json.dumps(records, indent=2), encoding="utf-8"
                )
            except Exception as error:  # one failed question must not end the run...
                meta["errors"].append({"id": question.id, "error": _error_text(error)})
                if isinstance(error, SETUP_ERRORS):  # ...unless every question would fail
                    _stop(meta, "api_refused")
                    raise RunHalted(
                        f"Stopped at {question.id}: the API refused the request "
                        f"({_error_text(error)}). Nothing more was sent.",
                        "api_refused",
                    ) from error
            _write_meta(run_dir, meta)
    except RunHalted as halted:
        halted.run_dir = run_dir
        raise
    finally:
        # Also on Ctrl+C, so run.json says how far the run got.
        _write_meta(run_dir, meta)
    return run_dir


def _run_batches(
    questions: list[EvalQuestion],
    run_dir: Path,
    meta: dict,
    max_cost_usd: float,
    *,
    index,
    client,
    model: str,
    judge_model: str,
    poll_seconds: float,
    sleep: Callable[[float], None],
    on_batch: Callable[[str, str], None] | None,
) -> None:
    """Batch mode: one batch for the answers, then one for every judge call.

    Writes the same files as a live run. A question with no passages gets the no-results answer
    with no request, as `answer` gives it. A request that fails, or a batch that cannot be sent,
    is recorded in `run.json` against its question and the rest go on.
    """

    def failed(id_: str, error: Exception) -> None:
        meta["errors"].append({"id": id_, "error": f"{type(error).__name__}: {error}"})

    def created(kind: str, batch_id: str) -> None:
        meta[f"{kind}_batch_id"] = batch_id
        _write_meta(run_dir, meta)
        if on_batch is not None:
            on_batch(kind, batch_id)

    # Search first, since each answer request carries its passages.
    found: dict[str, list[Chunk]] = {}
    searched: list[EvalQuestion] = []
    for question in questions:
        try:
            found[question.id] = index.search(question.question, k=SEARCH_K)
        except Exception as error:  # nothing was sent for it, so it is not counted as asked
            failed(question.id, error)
            continue
        searched.append(question)
    requests = [
        {"custom_id": q.id, "params": build_request(q.question, found[q.id], model)}
        for q in searched
        if found[q.id]
    ]
    if _estimate_passes_cap(meta, requests, model, max_cost_usd):
        _stop(meta, "batch_estimate")
        return

    # Counted as asked before the batch is sent, as in a live run.
    meta["asked_ids"] = [q.id for q in searched]
    saved: dict[str, dict] = {}

    def save_answer(id_: str, result: Answer) -> None:
        meta["answer_cost_usd"] += result.cost_usd
        text = card.to_json(result)
        (run_dir / ANSWERS_DIR / f"{id_}.json").write_text(text, encoding="utf-8")
        saved[id_] = json.loads(text)

    for question in searched:
        if not found[question.id]:
            save_answer(question.id, no_results_answer(question.question))
    _write_meta(run_dir, meta)

    if requests:
        results = _send(
            "answer", client, requests, [r["custom_id"] for r in requests], meta, failed,
            lambda b: created("answer", b), poll_seconds, sleep,
        )
        if results is not None:
            failures: list[BatchRequestFailed] = []
            for question in searched:
                if not found[question.id]:
                    continue
                try:
                    message = _message(results.get(question.id))
                    result = answer_from_response(
                        question.question, found[question.id], message, model=model, batch=True
                    )
                    save_answer(question.id, result)
                except BatchRequestFailed as error:
                    failures.append(error)
                    failed(question.id, error)
                except Exception as error:  # one failed answer must not end the run
                    failed(question.id, error)
            _write_meta(run_dir, meta)
            _halt_on_batch_errors(meta, "answer", failures, len(requests))
        _write_meta(run_dir, meta)

    _judge_in_batch(
        questions, saved, run_dir, meta, max_cost_usd, client=client, judge_model=judge_model,
        poll_seconds=poll_seconds, sleep=sleep, created=created, failed=failed,
    )


def _judge_in_batch(
    questions: list[EvalQuestion],
    saved: dict[str, dict],
    run_dir: Path,
    meta: dict,
    max_cost_usd: float,
    *,
    client,
    judge_model: str,
    poll_seconds: float,
    sleep: Callable[[float], None],
    created: Callable[[str, str], None],
    failed: Callable[[str, Exception], None],
) -> None:
    """Judge the saved answers, in question order, as one batch, and write their judge files.

    The batch is not sent if the cost so far plus its estimate passes `max_cost_usd`.
    """
    to_judge: dict[str, list[int]] = {}
    judge_batch: list[dict] = []
    for question in questions:
        if question.id not in saved:
            continue
        try:
            pairs = judge_requests(saved[question.id], judge_model)
        except Exception as error:
            failed(question.id, error)
            continue
        if not pairs:
            _write_records(run_dir, question.id, [])
            continue
        to_judge[question.id] = [sentence for sentence, _ in pairs]
        judge_batch += [
            {"custom_id": f"{question.id}-{sentence}", "params": request}
            for sentence, request in pairs
        ]
    if not judge_batch:
        return
    if _estimate_passes_cap(meta, judge_batch, judge_model, max_cost_usd):
        _stop(meta, "batch_estimate")
        return
    results = _send(
        "judge", client, judge_batch, list(to_judge), meta, failed,
        lambda b: created("judge", b), poll_seconds, sleep,
    )
    if results is None:
        return
    failures = []
    for id_, sentences in to_judge.items():
        try:
            records = []
            for sentence in sentences:
                try:
                    message = _message(results.get(f"{id_}-{sentence}"))
                except BatchRequestFailed as error:
                    failures.append(error)
                    records.append(unjudged_record(sentence, _sentence_case(str(error))))
                else:
                    records.append(judge_record(sentence, message))
            meta["judge_cost_usd"] += judge_cost(records, judge_model, batch=True)
            _write_records(run_dir, id_, records)
        except Exception as error:  # as in a live run, a judge error keeps the answer
            failed(id_, error)
    _halt_on_batch_errors(meta, "judge", failures, len(judge_batch))


REJUDGED_SUFFIX = "rejudged"


def rejudge_run(
    source_dir: Path,
    questions: list[EvalQuestion],
    out_dir: Path,
    client,
    max_cost_usd: float,
    *,
    judge_model: str,
    commit: str,
    dirty: bool,
    on_start: Callable[[Path], None] | None = None,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    on_batch: Callable[[str, str], None] | None = None,
) -> Path:
    """Judge a run's saved answers again, as one batch, in a new run directory; return it.

    No answer is asked again: each answer of `questions` saved in `source_dir` is copied as it
    is, then every cited sentence is judged with `judge_model`. The new `run.json` names the
    source run in `rejudged_from`, keeps its answering model, and counts no answer cost. The
    source run is not changed. A final run is refused, so the blind answers are judged once.
    """
    source_meta_path = source_dir / RUN_FILE
    if not source_meta_path.is_file():
        raise ValueError(f"No {RUN_FILE} in {source_dir}; give a folder made by `telltale eval`.")
    try:
        source_meta = json.loads(source_meta_path.read_text(encoding="utf-8"))
        model = source_meta["model"]
    except (ValueError, KeyError, TypeError) as error:  # ValueError: bad JSON or bad UTF-8
        raise ValueError(
            f"The {RUN_FILE} in {source_dir} cannot be read as a run ({error!r})."
        ) from error
    if source_meta.get("final"):
        raise ValueError(
            "That is a final run; its blind answers are judged once, so it is not re-judged."
        )
    require_judge_model(judge_model)

    # The source run's own selection, so its report reads the same; only questions given.
    chosen = set(source_meta.get("question_ids") or [q.id for q in questions])
    questions = [question for question in questions if question.id in chosen]
    source_asked = set(
        source_meta["asked_ids"] if "asked_ids" in source_meta else [q.id for q in questions]
    )
    started = datetime.now().astimezone()
    run_dir = _new_run_dir(
        out_dir, f"{started:%Y%m%d-%H%M}-{commit[:7]}-{model}-{REJUDGED_SUFFIX}"
    )
    (run_dir / ANSWERS_DIR).mkdir()
    (run_dir / JUDGE_DIR).mkdir()
    ids = {question.id for question in questions}
    meta = {
        "started": started.isoformat(timespec="seconds"),
        "commit": commit,
        "dirty": dirty,
        "model": model,
        "judge_model": judge_model,
        "final": False,
        "question_ids": [question.id for question in questions],
        "asked_ids": [question.id for question in questions if question.id in source_asked],
        "answer_cost_usd": 0.0,
        "judge_cost_usd": 0.0,
        "stopped_early": False,
        "errors": [e for e in source_meta.get("errors") or [] if e.get("id") in ids],
        "answer_batch_id": None,
        "judge_batch_id": None,
        "stop_reason": None,
        "rejudged_from": source_dir.name,
        "answer_commit": source_meta.get("commit"),
        "answer_dirty": source_meta.get("dirty"),
    }
    _write_meta(run_dir, meta)
    if on_start is not None:
        on_start(run_dir)
    saved: dict[str, dict] = {}
    for question in questions:
        path = source_dir / ANSWERS_DIR / f"{question.id}.json"
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            (run_dir / ANSWERS_DIR / f"{question.id}.json").write_text(text, encoding="utf-8")
            saved[question.id] = json.loads(text)

    def failed(id_: str, error: Exception) -> None:
        meta["errors"].append({"id": id_, "error": f"{type(error).__name__}: {error}"})

    def created(kind: str, batch_id: str) -> None:
        meta[f"{kind}_batch_id"] = batch_id
        _write_meta(run_dir, meta)
        if on_batch is not None:
            on_batch(kind, batch_id)

    try:
        _judge_in_batch(
            questions, saved, run_dir, meta, max_cost_usd, client=client,
            judge_model=judge_model, poll_seconds=poll_seconds, sleep=sleep, created=created,
            failed=failed,
        )
    except RunHalted as halted:
        halted.run_dir = run_dir
        raise
    finally:
        _write_meta(run_dir, meta)
    return run_dir


def _stop(meta: dict, reason: str) -> None:
    meta["stopped_early"] = True
    meta["stop_reason"] = reason


def _send(kind, client, requests, ids, meta, failed, on_created, poll_seconds, sleep):
    """Send one batch and wait for its results by custom id. A batch that cannot be sent is
    recorded against each of `ids` and gives None, unless the API refused it outright, which
    stops the run. A sent batch that can no longer be reached also stops the run."""
    try:
        return _send_batch(client, requests, on_created, poll_seconds, sleep, kind=kind)
    except BatchLost as lost:
        for id_ in ids:
            failed(id_, lost)
        _stop(meta, "batch_lost")
        raise RunHalted(str(lost), "batch_lost") from lost
    except Exception as error:
        for id_ in ids:
            failed(id_, error)
        if isinstance(error, SETUP_ERRORS):
            _stop(meta, "api_refused")
            raise RunHalted(
                f"The API refused the {kind} batch ({_error_text(error)}); nothing was sent.",
                "api_refused",
            ) from error
        return None


def _halt_on_batch_errors(
    meta: dict, kind: str, failures: list[BatchRequestFailed], sent: int
) -> None:
    """Stop the run when a request in the batch was refused as invalid, or every one errored:
    the same fault would be in every later request."""
    batch_id = meta[f"{kind}_batch_id"]
    invalid = [f for f in failures if f.error_type == "invalid_request_error"]
    errored = [f for f in failures if f.error_type is not None]
    if invalid:
        _stop(meta, "api_refused")
        raise RunHalted(
            f"The {kind} batch {batch_id} had a request refused as invalid ({invalid[0]}); "
            "the run stopped.",
            "api_refused",
        )
    if sent and len(errored) == sent:
        _stop(meta, "api_refused")
        raise RunHalted(
            f"Every request in the {kind} batch {batch_id} errored ({errored[0]}); "
            "the run stopped.",
            "api_refused",
        )


def _estimate_passes_cap(
    meta: dict, requests: list[dict], model: str, max_cost_usd: float
) -> bool:
    """True when the cost so far plus an estimate of this batch's cost passes the cap.

    This is an estimate, not a guaranteed bound. It counts every four characters of a request
    (as JSON) as one input token, which the real token count can pass, and every request as
    using its whole `max_tokens`, at the batch price.
    """
    estimate = sum(
        cost_usd(
            model,
            len(json.dumps(request["params"])) / CHARS_PER_TOKEN,
            request["params"]["max_tokens"],
            batch=True,
        )
        for request in requests
    )
    return meta["answer_cost_usd"] + meta["judge_cost_usd"] + estimate > max_cost_usd


class BatchLost(Exception):
    """A sent batch could no longer be checked or its results fetched."""


def _send_batch(
    client,
    requests: list[dict],
    on_created: Callable[[str], None],
    poll_seconds: float,
    sleep: Callable[[float], None],
    *,
    kind: str = "",
) -> dict:
    """Send one Message Batch, wait until it has ended, and return its results by custom id.

    Once the batch is sent, each check and the fetch of its results are tried again (after
    `sleep(poll_seconds)`) through network and server errors, up to MAX_WAIT_FAILURES times in a
    row. On giving up, or on any other error, `BatchLost` names the batch.
    """
    batches = client.messages.batches
    sent = batches.create(requests=requests)
    on_created(sent.id)

    def patiently(call):
        failures = 0
        while True:
            try:
                return call()
            except Exception as error:
                if not is_retryable(error) or failures >= MAX_WAIT_FAILURES:
                    raise BatchLost(
                        f"Lost touch with the {kind} batch {sent.id} ({_error_text(error)}). "
                        f"It was sent and is still charged; its results stay fetchable from "
                        f"Anthropic for {RESULTS_KEPT_DAYS} days after it was sent."
                    ) from error
                failures += 1
                sleep(poll_seconds)

    status = sent
    while status.processing_status != "ended":
        sleep(poll_seconds)
        status = patiently(lambda: batches.retrieve(sent.id))
    return patiently(lambda: {item.custom_id: item.result for item in batches.results(sent.id)})


def _message(result):
    """The message of a succeeded batch result; `BatchRequestFailed` for any other result."""
    if result is None:
        raise BatchRequestFailed("no result came back from the batch.")
    kind = getattr(result, "type", None)
    if kind == "succeeded":
        return result.message
    if kind == "errored":
        error = getattr(getattr(result, "error", None), "error", None)
        error_type = getattr(error, "type", None) or "unknown"
        raise BatchRequestFailed(
            f"the batch request errored: {error_type}: {getattr(error, 'message', '')}",
            error_type,
        )
    raise BatchRequestFailed(f"the batch request {kind}.")


def _sentence_case(text: str) -> str:
    return text[:1].upper() + text[1:]


def _write_records(run_dir: Path, id_: str, records: list[dict]) -> None:
    (run_dir / JUDGE_DIR / f"{id_}.json").write_text(
        json.dumps(records, indent=2), encoding="utf-8"
    )


def log_final_run(run_dir: Path, log: Path = DEFAULT_FINAL_LOG) -> None:
    """Add one line for this final run to the log: when, on which commit, and what it is set to
    ask. `run_eval` calls it before the first question; how far the run got is in `run.json`."""
    meta = json.loads((run_dir / RUN_FILE).read_text(encoding="utf-8"))
    entry = {
        "date": meta["started"],
        "commit": meta["commit"],
        "model": meta["model"],
        "judge_model": meta["judge_model"],
        "run_dir": str(run_dir),
        "question_ids": meta["question_ids"],
    }
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _new_run_dir(out_dir: Path, name: str) -> Path:
    """A new directory under `out_dir`; a second run in the same minute gets `-2`, and so on."""
    out_dir.mkdir(parents=True, exist_ok=True)
    base = safe_name(name)
    n = 1
    while True:
        candidate = out_dir / (base if n == 1 else f"{base}-{n}")
        try:
            candidate.mkdir()
        except FileExistsError:
            n += 1
            continue
        return candidate


def _write_meta(run_dir: Path, meta: dict) -> None:
    (run_dir / RUN_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")
