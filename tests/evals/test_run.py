"""The eval run: one question at a time, saved answers and verdicts, a cost cap, and the
final-run log. Synthetic questions and fake `ask` and `judge`, so no model is ever called."""

import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from telltale.answer import answer, build_request
from telltale.evals.judge import UnsupportedJudgeModel, build_judge_request, judge_answer
from telltale.evals.questions import EvalQuestion
from telltale.evals.report import write_report
from telltale.evals.run import DirtyTreeError, RunHalted, log_final_run, rejudge_run, run_eval
from telltale.models import Answer, Chunk, Citation, Sentence
from telltale.pricing import PRICES, UnknownModelPrice

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls, status: int, message: str = "Synthetic refusal."):
    """An SDK status error, as the client raises it for an HTTP error reply."""
    return cls(message, response=httpx2.Response(status, request=REQUEST), body=None)


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(message="Synthetic network drop.", request=REQUEST)


COMMIT = "abc1234def"
MODEL = "claude-test-answer"
JUDGE_MODEL = "claude-test-judge"
RUN_FIELDS = {
    "started", "commit", "dirty", "model", "judge_model", "final", "question_ids",
    "asked_ids", "answer_cost_usd", "judge_cost_usd", "stopped_early", "errors",
    "answer_batch_id", "judge_batch_id", "stop_reason",
}


def question(id_: str, blind: bool = False) -> EvalQuestion:
    return EvalQuestion(
        id=id_,
        question=f"Synthetic question {id_}?",
        expect="reports",
        expected_reports=("synthetic-report-a",),
        related_reports=(),
        blind=blind,
    )


def fake_ask(cost: float = 0.002, fail_on: str | None = None, asked: list[str] | None = None):
    def ask(text: str) -> Answer:
        if asked is not None:
            asked.append(text)
        if fail_on is not None and fail_on in text:
            raise RuntimeError("synthetic failure")
        return Answer(
            question=text,
            sentences=[
                Sentence(
                    text="A synthetic fact.",
                    citations=[
                        Citation(
                            report_title="Synthetic report A",
                            source="https://assets.example/report-a.pdf#page=2",
                            cited_text="A synthetic fact.",
                        )
                    ],
                )
            ],
            chunks=[],
            input_tokens=100,
            output_tokens=20,
            cost_usd=cost,
            stop_reason="end_turn",
        )

    return ask


def fake_judge(cost: float = 0.001, judged: list[dict] | None = None):
    def judge(saved: dict):
        if judged is not None:
            judged.append(saved)
        record = {
            "index": 0, "verdict": "supported", "reason": "stated",
            "input_tokens": 10, "output_tokens": 5,
        }
        return {0: "supported"}, [record], cost

    return judge


def run(questions, tmp_path: Path, *, ask=None, judge=None, max_cost: float = 1.0, **kw):
    options = {"model": MODEL, "judge_model": JUDGE_MODEL, "final": False, "commit": COMMIT,
               "dirty": False, "final_log": tmp_path / "final-runs.jsonl"}
    options.update(kw)
    return run_eval(
        questions, ask or fake_ask(), judge or fake_judge(), tmp_path / "runs", max_cost, **options
    )


def test_saves_one_answer_and_one_judge_file_per_question(tmp_path: Path):
    judged: list[dict] = []
    run_dir = run([question("Q1"), question("Q2")], tmp_path, judge=fake_judge(judged=judged))

    assert run_dir.parent == tmp_path / "runs"
    assert run_dir.name.endswith(f"-{COMMIT[:7]}-{MODEL}")
    for id_ in ("Q1", "Q2"):
        saved = json.loads((run_dir / "answers" / f"{id_}.json").read_text(encoding="utf-8"))
        assert saved["question"] == f"Synthetic question {id_}?"
        assert saved["sentences"][0]["text"] == "A synthetic fact."
        records = json.loads((run_dir / "judge" / f"{id_}.json").read_text(encoding="utf-8"))
        assert records == [{
            "index": 0, "verdict": "supported", "reason": "stated",
            "input_tokens": 10, "output_tokens": 5,
        }]
    # The judge is handed the saved answer itself.
    assert [s["question"] for s in judged] == ["Synthetic question Q1?", "Synthetic question Q2?"]

    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert set(meta) == RUN_FIELDS
    assert meta["commit"] == COMMIT
    assert meta["dirty"] is False
    assert meta["model"] == MODEL
    assert meta["judge_model"] == JUDGE_MODEL
    assert meta["final"] is False
    assert meta["question_ids"] == ["Q1", "Q2"]
    assert meta["asked_ids"] == ["Q1", "Q2"]
    assert meta["answer_cost_usd"] == pytest.approx(0.004)
    assert meta["judge_cost_usd"] == pytest.approx(0.002)
    assert meta["stopped_early"] is False
    assert meta["errors"] == []
    assert meta["started"]
    # A live run sends no batch.
    assert (meta["answer_batch_id"], meta["judge_batch_id"]) == (None, None)


def test_stops_before_the_next_question_once_cost_cap_is_reached(tmp_path: Path):
    asked: list[str] = []
    run_dir = run(
        [question("Q1"), question("Q2"), question("Q3")],
        tmp_path,
        ask=fake_ask(cost=0.004, asked=asked),
        judge=fake_judge(cost=0.002),
        max_cost=0.01,
    )

    assert asked == ["Synthetic question Q1?", "Synthetic question Q2?"]
    assert not (run_dir / "answers" / "Q3.json").exists()
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["stopped_early"] is True
    assert meta["question_ids"] == ["Q1", "Q2", "Q3"]
    assert meta["asked_ids"] == ["Q1", "Q2"]
    assert meta["answer_cost_usd"] == pytest.approx(0.008)
    assert meta["judge_cost_usd"] == pytest.approx(0.004)


def test_no_early_stop_when_the_cap_is_reached_on_the_last_question(tmp_path: Path):
    run_dir = run(
        [question("Q1"), question("Q2")],
        tmp_path,
        ask=fake_ask(cost=0.004),
        judge=fake_judge(cost=0.002),
        max_cost=0.01,
    )
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["stopped_early"] is False


def test_error_on_one_question_is_recorded_and_the_run_goes_on(tmp_path: Path):
    asked: list[str] = []
    run_dir = run(
        [question("Q1"), question("Q2"), question("Q3")],
        tmp_path,
        ask=fake_ask(fail_on="Q2", asked=asked),
    )

    assert len(asked) == 3
    assert (run_dir / "answers" / "Q1.json").exists()
    assert not (run_dir / "answers" / "Q2.json").exists()
    assert not (run_dir / "judge" / "Q2.json").exists()
    assert (run_dir / "answers" / "Q3.json").exists()
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["errors"] == [{"id": "Q2", "error": "RuntimeError: synthetic failure"}]
    # A question that failed was still asked.
    assert meta["asked_ids"] == ["Q1", "Q2", "Q3"]
    assert meta["answer_cost_usd"] == pytest.approx(0.004)


def test_a_judge_error_keeps_the_answer_and_its_cost(tmp_path: Path):
    def broken_judge(saved: dict):
        raise ValueError("synthetic judge failure")

    run_dir = run([question("Q1")], tmp_path, judge=broken_judge)

    assert (run_dir / "answers" / "Q1.json").exists()
    assert not (run_dir / "judge" / "Q1.json").exists()
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["errors"] == [{"id": "Q1", "error": "ValueError: synthetic judge failure"}]
    assert meta["answer_cost_usd"] == pytest.approx(0.002)


@pytest.mark.parametrize(
    "cls,status",
    [
        (anthropic.BadRequestError, 400),
        (anthropic.AuthenticationError, 401),
        (anthropic.PermissionDeniedError, 403),
        (anthropic.NotFoundError, 404),
    ],
)
def test_a_setup_error_while_asking_stops_the_run(tmp_path: Path, cls, status):
    """A request the API refuses outright would be refused for every question: stop, say so."""
    asked: list[str] = []
    ask = fake_ask(asked=asked)

    def refusing(text: str) -> Answer:
        if "Q2" in text:
            asked.append(text)
            raise status_error(cls, status)
        return ask(text)

    started: list[Path] = []
    with pytest.raises(RunHalted) as halted:
        run([question("Q1"), question("Q2"), question("Q3")], tmp_path, ask=refusing,
            on_start=started.append)

    (run_dir,) = started
    assert halted.value.run_dir == run_dir
    assert "Q2" in str(halted.value)
    assert cls.__name__ in str(halted.value)
    assert asked == ["Synthetic question Q1?", "Synthetic question Q2?"]
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["errors"] == [{"id": "Q2", "error": f"{cls.__name__}: Synthetic refusal."}]
    assert meta["asked_ids"] == ["Q1", "Q2"]
    assert meta["stopped_early"] is True
    assert meta["stop_reason"] == "api_refused"
    assert (run_dir / "answers" / "Q1.json").exists()


def test_a_setup_error_from_the_judge_stops_the_run(tmp_path: Path):
    def refusing_judge(saved: dict):
        raise status_error(anthropic.BadRequestError, 400)

    asked: list[str] = []
    with pytest.raises(RunHalted):
        run([question("Q1"), question("Q2")], tmp_path, ask=fake_ask(asked=asked),
            judge=refusing_judge)
    assert asked == ["Synthetic question Q1?"]


def test_a_retryable_error_on_one_question_is_recorded_and_the_run_goes_on(tmp_path: Path):
    ask = fake_ask()

    def flaky(text: str) -> Answer:
        if "Q1" in text:
            raise status_error(anthropic.InternalServerError, 500, "Synthetic server error.")
        return ask(text)

    run_dir = run([question("Q1"), question("Q2")], tmp_path, ask=flaky)

    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["errors"] == [{"id": "Q1", "error": "InternalServerError: Synthetic server error."}]
    assert meta["stopped_early"] is False
    assert meta["stop_reason"] is None
    assert (run_dir / "answers" / "Q2.json").exists()


def test_the_cost_cap_is_named_as_the_stop_reason(tmp_path: Path):
    run_dir = run([question("Q1"), question("Q2")], tmp_path, ask=fake_ask(cost=0.02),
                  max_cost=0.01)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["stop_reason"] == "cost_cap"


def test_final_run_refuses_a_dirty_tree(tmp_path: Path):
    asked: list[str] = []
    with pytest.raises(DirtyTreeError):
        run([question("B1", blind=True)], tmp_path, ask=fake_ask(asked=asked), final=True,
            dirty=True)
    assert asked == []
    assert not (tmp_path / "runs").exists()


def test_working_run_on_a_dirty_tree_is_recorded_as_dirty(tmp_path: Path):
    run_dir = run([question("Q1")], tmp_path, dirty=True)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["dirty"] is True


def test_final_run_is_logged(tmp_path: Path):
    log = tmp_path / "logs" / "final-runs.jsonl"
    run_dir = run([question("B1", blind=True)], tmp_path, final=True, final_log=log)

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert entry["commit"] == COMMIT
    assert entry["date"] == meta["started"]
    assert entry["run_dir"] == str(run_dir)
    assert entry["question_ids"] == ["B1"]


def test_working_run_is_not_logged(tmp_path: Path):
    log = tmp_path / "final-runs.jsonl"
    run([question("Q1")], tmp_path, final_log=log)
    assert not log.exists()


def test_final_run_is_logged_before_the_first_question(tmp_path: Path):
    log = tmp_path / "final-runs.jsonl"
    seen: list[bool] = []

    def ask(text: str) -> Answer:
        seen.append(log.exists())
        return fake_ask()(text)

    run([question("B1", blind=True)], tmp_path, ask=ask, final=True, final_log=log)
    assert seen == [True]


def test_interrupt_during_a_final_run_still_logs_it(tmp_path: Path):
    log = tmp_path / "final-runs.jsonl"
    asked: list[str] = []
    started: list[Path] = []

    def ask(text: str) -> Answer:
        asked.append(text)
        if len(asked) == 2:
            raise KeyboardInterrupt
        return fake_ask()(text)

    with pytest.raises(KeyboardInterrupt):
        run(
            [question("B1", blind=True), question("B2", blind=True), question("B3", blind=True)],
            tmp_path,
            ask=ask,
            final=True,
            final_log=log,
            on_start=started.append,
        )

    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [e["question_ids"] for e in entries] == [["B1", "B2", "B3"]]
    (run_dir,) = started
    assert entries[0]["run_dir"] == str(run_dir)
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    # The interrupted question was sent, so it counts as asked; the third was not.
    assert meta["asked_ids"] == ["B1", "B2"]
    assert (run_dir / "answers" / "B1.json").exists()


def test_on_start_is_told_the_run_directory_before_the_first_question(tmp_path: Path):
    order: list[str] = []

    def ask(text: str) -> Answer:
        order.append("ask")
        return fake_ask()(text)

    run_dir = run(
        [question("Q1")], tmp_path, ask=ask, on_start=lambda d: order.append(f"start:{d}")
    )
    assert order == [f"start:{run_dir}", "ask"]


def test_run_directory_name_is_safe_for_a_filesystem(tmp_path: Path):
    run_dir = run([question("Q1")], tmp_path, model="vendor/model:large v2")
    assert run_dir.parent == tmp_path / "runs"
    assert "/" not in run_dir.name and ":" not in run_dir.name and " " not in run_dir.name


def test_two_runs_in_the_same_minute_do_not_share_a_directory(tmp_path: Path):
    first = run([question("Q1")], tmp_path)
    second = run([question("Q1")], tmp_path)
    assert first != second
    assert (first / "run.json").exists() and (second / "run.json").exists()


def test_an_id_unsafe_for_a_file_name_is_refused_before_any_question(tmp_path: Path):
    asked: list[str] = []
    with pytest.raises(ValueError, match="Q/2"):
        run([question("Q1"), question("Q/2")], tmp_path, ask=fake_ask(asked=asked))
    assert asked == []



# --- batch mode -----------------------------------------------------------------------------
# A fake index, and fake clients that answer every request with the same canned replies, so a
# live run and a batch run of the same questions can be compared file by file.

BATCH_MODEL = "claude-haiku-5-5"
BATCH_JUDGE = "claude-sonnet-5-5"
POLL_SECONDS = 30.0
CHUNK = Chunk(
    id=1,
    report_slug="synthetic-report-a",
    report_title="Synthetic report A",
    url="https://assets.example/report-a.pdf",
    page=2,
    passages=["A synthetic passage about the job."],
)


def nothing_found(id_: str) -> EvalQuestion:
    """A question the fake index finds no passage for."""
    return EvalQuestion(
        id=id_,
        question=f"Synthetic question {id_} that finds nothing?",
        expect="no_answer",
        expected_reports=(),
        related_reports=(),
        blind=False,
    )


class FakeIndex:
    def __init__(self, fail_on: str | None = None):
        self.queries: list[tuple[str, int]] = []
        self.fail_on = fail_on

    def search(self, query: str, k: int = 8) -> list[Chunk]:
        self.queries.append((query, k))
        if self.fail_on is not None and self.fail_on in query:
            raise RuntimeError("synthetic search failure")
        return [] if "finds nothing" in query else [CHUNK]


def reply_to(params: dict):
    """The canned model reply to one request: a verdict for a judge request, otherwise a cited
    sentence and an uncited one."""
    if "output_config" in params:
        return SimpleNamespace(
            content=[SimpleNamespace(
                type="text", citations=None,
                text=json.dumps({"verdict": "supported", "reason": "Stated in the passage."}),
            )],
            usage=SimpleNamespace(input_tokens=1_000, output_tokens=100),
            stop_reason="end_turn",
        )
    result = params["messages"][0]["content"][0]
    citation = SimpleNamespace(
        type="search_result_location", source=result["source"], title=result["title"],
        cited_text=result["content"][1]["text"],
    )
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="A synthetic fact about the job.",
                            citations=[citation]),
            SimpleNamespace(type="text", text=" Nobody noticed the change in time.",
                            citations=None),
        ],
        usage=SimpleNamespace(input_tokens=10_000, output_tokens=500),
        stop_reason="end_turn",
    )


class LiveClient:
    """A stand-in for `client.messages.create`, for the live run to compare against."""

    def __init__(self):
        self.messages = SimpleNamespace(create=lambda **params: reply_to(params))


def errored(message: str = "Synthetic overload."):
    return SimpleNamespace(
        type="errored",
        error=SimpleNamespace(
            type="error", error=SimpleNamespace(type="overloaded_error", message=message)
        ),
    )


class FakeBatches:
    """`client.messages.batches`: each batch is in progress for `polls` retrievals, then ended.
    `outcome` replaces the result of a custom id ("missing" leaves it out). Results come back in
    reverse order, since the API does not keep the request order."""

    def __init__(self, polls: int = 2, outcome: dict | None = None, create_error=None,
                 on_create=None, retrieve_errors=(), results_errors=()):
        self.retrieve_errors = list(retrieve_errors)
        self.results_errors = list(results_errors)
        self.polls = polls
        self.outcome = outcome or {}
        self.create_error = create_error
        self.on_create = on_create
        self.created: list[list[dict]] = []
        self.retrieved: list[str] = []
        self._left: dict[str, int] = {}

    def create(self, *, requests):
        if self.on_create is not None:
            self.on_create()
        if self.create_error is not None:
            raise self.create_error
        self.created.append(list(requests))
        batch_id = f"msgbatch_test_{len(self.created)}"
        self._left[batch_id] = self.polls
        return SimpleNamespace(id=batch_id, processing_status="in_progress")

    def retrieve(self, batch_id: str):
        self.retrieved.append(batch_id)
        if self.retrieve_errors:
            raise self.retrieve_errors.pop(0)
        self._left[batch_id] -= 1
        status = "ended" if self._left[batch_id] <= 0 else "in_progress"
        return SimpleNamespace(id=batch_id, processing_status=status)

    def results(self, batch_id: str):
        if self.results_errors:
            raise self.results_errors.pop(0)
        requests = self.created[int(batch_id.rsplit("_", 1)[1]) - 1]
        for request in reversed(requests):
            given = self.outcome.get(request["custom_id"])
            if given == "missing":
                continue
            result = given or SimpleNamespace(type="succeeded", message=reply_to(request["params"]))
            yield SimpleNamespace(custom_id=request["custom_id"], result=result)


class FakeBatchClient:
    def __init__(self, **kw):
        self.messages = SimpleNamespace(batches=FakeBatches(**kw))

    @property
    def batches(self) -> FakeBatches:
        return self.messages.batches


def run_batch(questions, tmp_path: Path, client, *, index=None, max_cost: float = 1.0,
              sleeps: list | None = None, **kw):
    options = {"model": BATCH_MODEL, "judge_model": BATCH_JUDGE, "final": False,
               "commit": COMMIT, "dirty": False, "final_log": tmp_path / "final-runs.jsonl"}
    options.update(kw)
    return run_eval(
        questions, None, None, tmp_path / "batch-runs", max_cost,
        batch=True, index=index or FakeIndex(), client=client,
        poll_seconds=POLL_SECONDS, sleep=(sleeps if sleeps is not None else []).append,
        **options,
    )


def run_live(questions, tmp_path: Path):
    index, client = FakeIndex(), LiveClient()
    return run_eval(
        questions,
        lambda text: answer(text, index, client, BATCH_MODEL),
        lambda saved: judge_answer(saved, client, BATCH_JUDGE),
        tmp_path / "live-runs", 1.0,
        model=BATCH_MODEL, judge_model=BATCH_JUDGE, final=False, commit=COMMIT, dirty=False,
        final_log=tmp_path / "final-runs.jsonl",
    )


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def estimate(params: dict, model: str) -> float:
    """Controller ruling 1: characters / 4 input tokens plus max_tokens output tokens, at the
    model's price, halved for the batch discount."""
    price_in, price_out = PRICES[model]
    chars = len(json.dumps(params))
    return (chars / 4 * price_in + params["max_tokens"] * price_out) / 1e6 / 2


def test_batch_run_saves_the_same_files_as_a_live_run(tmp_path: Path):
    questions = [question("Q1"), question("Q2"), nothing_found("Q3")]
    client = FakeBatchClient()
    sleeps: list[float] = []

    live_dir = run_live(questions, tmp_path)
    batch_dir = run_batch(questions, tmp_path, client, sleeps=sleeps)

    for id_ in ("Q1", "Q2", "Q3"):
        live = read(live_dir / "answers" / f"{id_}.json")
        batched = read(batch_dir / "answers" / f"{id_}.json")
        assert batched["cost_usd"] == pytest.approx(live.pop("cost_usd") / 2)
        batched.pop("cost_usd")
        assert batched == live
        assert read(batch_dir / "judge" / f"{id_}.json") == read(live_dir / "judge" / f"{id_}.json")
    # The no-results question was answered without a request, and only cited sentences judged.
    [answers, judging] = client.batches.created
    assert [r["custom_id"] for r in answers] == ["Q1", "Q2"]
    assert answers[0]["params"] == build_request("Synthetic question Q1?", [CHUNK], BATCH_MODEL)
    assert [r["custom_id"] for r in judging] == ["Q1-0", "Q2-0"]
    assert judging[0]["params"] == build_judge_request(
        "A synthetic fact about the job.",
        [("Synthetic report A", "A synthetic passage about the job.")], BATCH_JUDGE
    )
    # Each batch was polled until it ended, with the injected sleep.
    assert sleeps == [POLL_SECONDS] * 4
    assert client.batches.retrieved == ["msgbatch_test_1"] * 2 + ["msgbatch_test_2"] * 2

    live_meta, batch_meta = read(live_dir / "run.json"), read(batch_dir / "run.json")
    assert set(batch_meta) == RUN_FIELDS
    assert batch_meta["answer_batch_id"] == "msgbatch_test_1"
    assert batch_meta["judge_batch_id"] == "msgbatch_test_2"
    for field in ("question_ids", "asked_ids", "errors", "stopped_early", "model", "judge_model"):
        assert batch_meta[field] == live_meta[field]
    # The report reads a batch run as it reads a live one.
    write_report(batch_dir, questions)
    assert (batch_dir / "report.md").is_file()


def test_batch_cost_is_half_the_live_cost(tmp_path: Path):
    questions = [question("Q1"), question("Q2"), nothing_found("Q3")]

    live = read(run_live(questions, tmp_path) / "run.json")
    batched = read(run_batch(questions, tmp_path, FakeBatchClient()) / "run.json")

    # Two answers of 10,000 input and 500 output tokens at $0.10 and $0.50 per million, halved.
    assert batched["answer_cost_usd"] == pytest.approx(0.00125)
    assert live["answer_cost_usd"] == pytest.approx(0.0025)
    # Two verdicts of 1,000 input and 100 output tokens at $2 and $10 per million, halved.
    assert batched["judge_cost_usd"] == pytest.approx(0.003)
    assert live["judge_cost_usd"] == pytest.approx(0.006)


def test_errored_batch_item_is_recorded_and_others_kept(tmp_path: Path):
    questions = [question("Q1"), question("Q2"), question("Q3"), question("Q4")]
    client = FakeBatchClient(
        outcome={
            "Q2": errored(),
            "Q3": "missing",
            "Q1-0": SimpleNamespace(type="expired"),
            "Q4-0": errored("Synthetic judge overload."),
        }
    )

    run_dir = run_batch(questions, tmp_path, client)

    meta = read(run_dir / "run.json")
    assert meta["errors"] == [
        {"id": "Q2", "error": "BatchRequestFailed: the batch request errored: "
                              "overloaded_error: Synthetic overload."},
        {"id": "Q3", "error": "BatchRequestFailed: no result came back from the batch."},
    ]
    # A failed answer is sent for asking, so it counts as asked, like a live failure.
    assert meta["asked_ids"] == ["Q1", "Q2", "Q3", "Q4"]
    assert sorted(p.name for p in (run_dir / "answers").iterdir()) == ["Q1.json", "Q4.json"]
    # Only the saved answers are judged; a failed verdict is unjudged and costs nothing.
    [_, judging] = client.batches.created
    assert [r["custom_id"] for r in judging] == ["Q1-0", "Q4-0"]
    assert read(run_dir / "judge" / "Q1.json") == [{
        "index": 0, "verdict": "unjudged", "reason": "The batch request expired.",
        "input_tokens": 0, "output_tokens": 0,
    }]
    [q4] = read(run_dir / "judge" / "Q4.json")
    assert q4["verdict"] == "unjudged"
    assert q4["reason"] == (
        "The batch request errored: overloaded_error: Synthetic judge overload."
    )
    # Q1 and Q4 at $0.000625 each; Q2 and Q3 cost nothing on record.
    assert meta["answer_cost_usd"] == pytest.approx(0.00125)
    assert meta["judge_cost_usd"] == 0.0
    assert not (run_dir / "judge" / "Q2.json").exists()


def test_a_batch_that_cannot_be_sent_is_recorded_for_each_of_its_questions(tmp_path: Path):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages/batches")
    outage = anthropic.APIError("Synthetic outage.", request, body=None)
    client = FakeBatchClient(create_error=outage)

    run_dir = run_batch([question("Q1"), nothing_found("Q2"), question("Q3")], tmp_path, client)

    meta = read(run_dir / "run.json")
    assert meta["errors"] == [
        {"id": "Q1", "error": "APIError: Synthetic outage."},
        {"id": "Q3", "error": "APIError: Synthetic outage."},
    ]
    assert meta["answer_batch_id"] is None
    assert meta["judge_batch_id"] is None
    # The no-results answer needs no request, so it is still saved and judged (nothing to judge).
    assert read(run_dir / "answers" / "Q2.json")["stop_reason"] == "no_results"
    assert read(run_dir / "judge" / "Q2.json") == []


def test_a_search_failure_is_recorded_and_the_rest_are_sent(tmp_path: Path):
    client = FakeBatchClient()

    run_dir = run_batch([question("Q1"), question("Q2")], tmp_path, client,
                        index=FakeIndex(fail_on="Q1"))

    meta = read(run_dir / "run.json")
    assert meta["errors"] == [{"id": "Q1", "error": "RuntimeError: synthetic search failure"}]
    assert meta["asked_ids"] == ["Q2"]
    assert [r["custom_id"] for r in client.batches.created[0]] == ["Q2"]


def test_answer_batch_over_the_cost_cap_is_not_sent(tmp_path: Path):
    questions = [question("Q1"), question("Q2")]
    whole = sum(
        estimate(build_request(q.question, [CHUNK], BATCH_MODEL), BATCH_MODEL) for q in questions
    )
    client = FakeBatchClient()

    run_dir = run_batch(questions, tmp_path, client, max_cost=whole * 0.99)

    assert client.batches.created == []
    meta = read(run_dir / "run.json")
    assert meta["stopped_early"] is True
    assert meta["asked_ids"] == []
    assert meta["answer_batch_id"] is None
    assert list((run_dir / "answers").iterdir()) == []


def test_judge_batch_over_the_cost_cap_is_not_sent(tmp_path: Path):
    questions = [question("Q1"), question("Q2"), nothing_found("Q3")]
    answers = sum(
        estimate(build_request(q.question, [CHUNK], BATCH_MODEL), BATCH_MODEL)
        for q in questions[:2]
    )
    client = FakeBatchClient()

    # Room for the answer batch, not for the judge batch on top of what the answers cost.
    run_dir = run_batch(questions, tmp_path, client, max_cost=answers * 1.01)

    assert len(client.batches.created) == 1
    meta = read(run_dir / "run.json")
    assert meta["stopped_early"] is True
    assert meta["asked_ids"] == ["Q1", "Q2", "Q3"]
    assert meta["judge_batch_id"] is None
    assert meta["judge_cost_usd"] == 0.0
    assert sorted(p.name for p in (run_dir / "answers").iterdir()) == [
        "Q1.json", "Q2.json", "Q3.json"
    ]
    # Only the answer with nothing to judge has its (empty) judge file.
    assert [p.name for p in (run_dir / "judge").iterdir()] == ["Q3.json"]


def test_judge_batch_within_the_cost_cap_is_sent(tmp_path: Path):
    questions = [question("Q1")]
    answers = estimate(build_request("Synthetic question Q1?", [CHUNK], BATCH_MODEL), BATCH_MODEL)
    judging = estimate(
        build_judge_request(
            "A synthetic fact about the job.",
        [("Synthetic report A", "A synthetic passage about the job.")], BATCH_JUDGE
        ),
        BATCH_JUDGE,
    )
    client = FakeBatchClient()

    # The answer really costs $0.000625; the judge estimate fits on top of that.
    cap = max(answers, 0.000625 + judging) * 1.01
    run_dir = run_batch(questions, tmp_path, client, max_cost=cap)

    assert len(client.batches.created) == 2
    assert read(run_dir / "run.json")["stopped_early"] is False


def test_interrupt_while_waiting_keeps_the_batch_id(tmp_path: Path):
    def interrupted(seconds: float):
        raise KeyboardInterrupt

    started: list[Path] = []
    client = FakeBatchClient()
    with pytest.raises(KeyboardInterrupt):
        run_eval(
            [question("Q1")], None, None, tmp_path / "runs", 1.0,
            model=BATCH_MODEL, judge_model=BATCH_JUDGE, final=False, commit=COMMIT, dirty=False,
            final_log=tmp_path / "final-runs.jsonl", on_start=started.append,
            batch=True, index=FakeIndex(), client=client, sleep=interrupted,
        )

    (run_dir,) = started
    meta = read(run_dir / "run.json")
    assert meta["answer_batch_id"] == "msgbatch_test_1"
    assert meta["asked_ids"] == ["Q1"]


def test_final_batch_run_is_logged_before_the_batch_is_sent(tmp_path: Path):
    log = tmp_path / "final-runs.jsonl"
    seen: list[bool] = []
    client = FakeBatchClient(on_create=lambda: seen.append(log.exists()))

    run_batch([question("B1", blind=True)], tmp_path, client, final=True, final_log=log)

    assert seen == [True, True]
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_batch_run_refuses_an_unpriced_model_before_any_batch(tmp_path: Path):
    client = FakeBatchClient()
    for models in ({"model": "claude-unpriced-test"}, {"judge_model": "claude-unpriced-test"}):
        with pytest.raises(UnknownModelPrice, match="claude-unpriced-test"):
            run_batch([question("Q1")], tmp_path, client, **models)
    assert client.batches.created == []
    assert not (tmp_path / "batch-runs").exists()


def test_batch_run_refuses_a_judge_model_with_no_thinking_off_setting(tmp_path, monkeypatch):
    monkeypatch.setitem(PRICES, "claude-unlisted-test", (1.0, 1.0))
    client = FakeBatchClient()
    with pytest.raises(UnsupportedJudgeModel, match="claude-unlisted-test"):
        run_batch([question("Q1")], tmp_path, client, judge_model="claude-unlisted-test")
    assert client.batches.created == []
    assert not (tmp_path / "batch-runs").exists()


def test_batch_run_needs_an_index_and_a_client(tmp_path: Path):
    with pytest.raises(ValueError, match="index"):
        run_eval([question("Q1")], None, None, tmp_path / "runs", 1.0, model=BATCH_MODEL,
                 judge_model=BATCH_JUDGE, final=False, commit=COMMIT, dirty=False, batch=True)
    assert not (tmp_path / "runs").exists()


def test_answer_batch_over_the_cost_cap_names_the_estimate_as_the_stop_reason(tmp_path: Path):
    run_dir = run_batch([question("Q1")], tmp_path, FakeBatchClient(), max_cost=0.0000001)
    meta = read(run_dir / "run.json")
    assert meta["stopped_early"] is True
    assert meta["stop_reason"] == "batch_estimate"


def test_a_batch_refused_as_invalid_when_sent_stops_the_run(tmp_path: Path):
    client = FakeBatchClient(create_error=status_error(anthropic.BadRequestError, 400))

    with pytest.raises(RunHalted, match="BadRequestError"):
        run_batch([question("Q1"), question("Q2")], tmp_path, client)
    (run_dir,) = (tmp_path / "batch-runs").iterdir()
    meta = read(run_dir / "run.json")
    assert [e["id"] for e in meta["errors"]] == ["Q1", "Q2"]
    assert meta["stop_reason"] == "api_refused"


def invalid(message: str = "Synthetic invalid field."):
    return SimpleNamespace(
        type="errored",
        error=SimpleNamespace(
            type="error", error=SimpleNamespace(type="invalid_request_error", message=message)
        ),
    )


def test_an_invalid_judge_request_in_the_batch_stops_the_run(tmp_path: Path):
    client = FakeBatchClient(outcome={"Q2-0": invalid()})

    with pytest.raises(RunHalted) as halted:
        run_batch([question("Q1"), question("Q2")], tmp_path, client)

    assert "msgbatch_test_2" in str(halted.value)
    assert "invalid_request_error" in str(halted.value)
    run_dir = halted.value.run_dir
    # What came back is still saved: Q1 judged, Q2 unjudged.
    assert read(run_dir / "judge" / "Q1.json")[0]["verdict"] == "supported"
    assert read(run_dir / "judge" / "Q2.json")[0]["verdict"] == "unjudged"
    meta = read(run_dir / "run.json")
    assert meta["stop_reason"] == "api_refused"
    assert meta["judge_batch_id"] == "msgbatch_test_2"


def test_every_judge_request_errored_stops_the_run(tmp_path: Path):
    client = FakeBatchClient(outcome={"Q1-0": errored(), "Q2-0": errored()})

    with pytest.raises(RunHalted, match="Every request in the judge batch msgbatch_test_2"):
        run_batch([question("Q1"), question("Q2")], tmp_path, client)


def test_one_overloaded_judge_request_does_not_stop_the_run(tmp_path: Path):
    client = FakeBatchClient(outcome={"Q1-0": errored()})

    run_dir = run_batch([question("Q1"), question("Q2")], tmp_path, client)

    assert read(run_dir / "judge" / "Q1.json")[0]["verdict"] == "unjudged"
    assert read(run_dir / "judge" / "Q2.json")[0]["verdict"] == "supported"
    assert read(run_dir / "run.json")["stop_reason"] is None


def test_an_invalid_answer_request_in_the_batch_stops_the_run(tmp_path: Path):
    client = FakeBatchClient(outcome={"Q1": invalid()})

    with pytest.raises(RunHalted, match="answer batch msgbatch_test_1"):
        run_batch([question("Q1"), question("Q2")], tmp_path, client)
    # The judge batch is never sent.
    assert len(client.batches.created) == 1


def test_waiting_on_a_batch_keeps_polling_through_network_and_server_errors(tmp_path: Path):
    blips = [
        connection_error(),
        status_error(anthropic.InternalServerError, 500),
        status_error(anthropic.OverloadedError, 529),
    ]
    client = FakeBatchClient(polls=1, retrieve_errors=blips,
                             results_errors=[connection_error()])
    sleeps: list[float] = []

    run_dir = run_batch([question("Q1")], tmp_path, client, sleeps=sleeps)

    meta = read(run_dir / "run.json")
    assert meta["errors"] == []
    assert read(run_dir / "judge" / "Q1.json")[0]["verdict"] == "supported"
    # Answer batch: one sleep before its check, one after each of the three failed checks and
    # one after the failed fetch of its results. Judge batch: one sleep before its check.
    assert sleeps == [POLL_SECONDS] * 6


def test_losing_touch_with_a_sent_batch_names_it_and_says_results_stay_fetchable(
    tmp_path: Path,
):
    client = FakeBatchClient(polls=1, retrieve_errors=[connection_error()] * 100)

    with pytest.raises(RunHalted) as halted:
        run_batch([question("Q1")], tmp_path, client)

    message = str(halted.value)
    assert "msgbatch_test_1" in message
    assert "29 days" in message
    meta = read(halted.value.run_dir / "run.json")
    assert meta["answer_batch_id"] == "msgbatch_test_1"
    assert meta["stop_reason"] == "batch_lost"
    assert [e["id"] for e in meta["errors"]] == ["Q1"]
    # It gave up after a bounded number of tries, not on the first blip.
    assert 1 < len(client.batches.retrieved) < 100


def test_a_refusal_while_waiting_is_not_retried(tmp_path: Path):
    client = FakeBatchClient(polls=1, retrieve_errors=[status_error(anthropic.NotFoundError, 404)])

    with pytest.raises(RunHalted, match="msgbatch_test_1"):
        run_batch([question("Q1")], tmp_path, client)
    assert client.batches.retrieved == ["msgbatch_test_1"]


def test_batch_run_through_the_real_sdk_without_network(tmp_path: Path):
    """The installed SDK's Message Batches calls over a mock transport: create, poll with
    retrieve, then read the JSONL results, matched back by custom id."""
    created: list[dict] = []
    polled: dict[str, int] = {}

    def batch_json(batch_id: str, status: str) -> dict:
        ended = status == "ended"
        return {
            "id": batch_id, "type": "message_batch", "processing_status": status,
            "request_counts": {"processing": 0 if ended else 1, "succeeded": 1 if ended else 0,
                               "errored": 0, "canceled": 0, "expired": 0},
            "created_at": "2026-11-19T00:00:00Z", "expires_at": "2026-11-20T00:00:00Z",
            "ended_at": "2026-11-19T01:00:00Z" if ended else None,
            "archived_at": None, "cancel_initiated_at": None,
            "results_url": f"https://api.anthropic.com/v1/messages/batches/{batch_id}/results"
            if ended else None,
        }

    def message_json(params: dict) -> dict:
        if "output_config" in params:
            content = [{"type": "text", "text": json.dumps(
                {"verdict": "partly", "reason": "One claim is not stated."})}]
            usage, stop = {"input_tokens": 1_000, "output_tokens": 100}, "end_turn"
        else:
            result = params["messages"][0]["content"][0]
            content = [{"type": "text", "text": "A synthetic fact about the job.", "citations": [{
                "type": "search_result_location", "cited_text": result["content"][1]["text"],
                "source": result["source"], "title": result["title"], "search_result_index": 0,
                "start_block_index": 1, "end_block_index": 2,
            }]}]
            usage, stop = {"input_tokens": 10_000, "output_tokens": 500}, "end_turn"
        return {"id": "msg_test", "type": "message", "role": "assistant",
                "model": params["model"], "content": content, "stop_reason": stop,
                "stop_sequence": None, "usage": usage}

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/messages/batches":
            created.append(json.loads(request.content))
            batch_id = f"msgbatch_test_{len(created)}"
            polled[batch_id] = 0
            return httpx2.Response(200, json=batch_json(batch_id, "in_progress"))
        batch_id = path.split("/")[4]
        if path.endswith("/results"):
            lines = [
                json.dumps({"custom_id": r["custom_id"],
                            "result": {"type": "succeeded", "message": message_json(r["params"])}})
                for r in created[int(batch_id.rsplit("_", 1)[1]) - 1]["requests"]
            ]
            return httpx2.Response(200, content="\n".join(lines).encode())
        polled[batch_id] += 1
        return httpx2.Response(200, json=batch_json(batch_id, "ended"))

    client = anthropic.Anthropic(
        api_key="test-key",
        base_url="https://api.anthropic.com",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )

    run_dir = run_batch([question("Q1")], tmp_path, client)

    assert [[r["custom_id"] for r in body["requests"]] for body in created] == [["Q1"], ["Q1-0"]]
    assert polled["msgbatch_test_1"] >= 1
    saved = read(run_dir / "answers" / "Q1.json")
    assert saved["sentences"][0]["citations"][0]["cited_text"] == (
        "A synthetic passage about the job."
    )
    assert read(run_dir / "judge" / "Q1.json")[0]["verdict"] == "partly"
    meta = read(run_dir / "run.json")
    assert meta["errors"] == []
    assert meta["answer_cost_usd"] == pytest.approx(0.000625)
    assert meta["judge_cost_usd"] == pytest.approx(0.0015)


# --- re-judging a run's saved answers -------------------------------------------------------


def rejudge(source: Path, questions, tmp_path: Path, client, *, max_cost: float = 1.0, **kw):
    options = {"judge_model": BATCH_JUDGE, "commit": "fff9999aaa", "dirty": False}
    options.update(kw)
    return rejudge_run(
        source, questions, tmp_path / "rejudged", client, max_cost,
        poll_seconds=POLL_SECONDS, sleep=[].append, **options,
    )


def test_rejudge_sends_only_a_judge_batch_and_keeps_the_answers(tmp_path: Path):
    questions = [question("Q1"), question("Q2")]
    source = run_batch(questions, tmp_path, FakeBatchClient())
    client = FakeBatchClient()

    run_dir = rejudge(source, questions, tmp_path, client)

    # No answer is asked again: the only batch is the judge batch.
    [judging] = client.batches.created
    assert [r["custom_id"] for r in judging] == ["Q1-0", "Q2-0"]
    assert judging[0]["params"] == build_judge_request(
        "A synthetic fact about the job.",
        [("Synthetic report A", "A synthetic passage about the job.")], BATCH_JUDGE,
    )
    for id_ in ("Q1", "Q2"):
        assert (run_dir / "answers" / f"{id_}.json").read_bytes() == (
            source / "answers" / f"{id_}.json"
        ).read_bytes()
        assert read(run_dir / "judge" / f"{id_}.json")[0]["verdict"] == "supported"
    meta = read(run_dir / "run.json")
    assert meta["rejudged_from"] == source.name
    assert meta["model"] == BATCH_MODEL
    assert meta["judge_model"] == BATCH_JUDGE
    assert meta["commit"] == "fff9999aaa"
    # The answers' own commit is kept, and the report says where they came from.
    assert meta["answer_commit"] == COMMIT
    assert meta["answer_dirty"] is False
    assert meta["answer_cost_usd"] == 0.0
    assert meta["judge_cost_usd"] > 0
    assert meta["asked_ids"] == ["Q1", "Q2"]
    assert meta["judge_batch_id"] == "msgbatch_test_1"
    assert run_dir.name.endswith("-rejudged")
    # The source run is left as it was.
    assert read(source / "run.json")["judge_batch_id"] == "msgbatch_test_2"
    # The report reads a re-judged run like any other, and names the source run.
    report = write_report(run_dir, questions).read_text(encoding="utf-8")
    assert f"Re-judged from: {source.name} (answers from commit {COMMIT})" in report


def test_rejudge_only_takes_the_questions_given(tmp_path: Path):
    questions = [question("Q1"), question("Q2")]
    source = run_batch(questions, tmp_path, FakeBatchClient())
    client = FakeBatchClient()

    run_dir = rejudge(source, [question("Q2")], tmp_path, client)

    [judging] = client.batches.created
    assert [r["custom_id"] for r in judging] == ["Q2-0"]
    assert [p.name for p in (run_dir / "answers").iterdir()] == ["Q2.json"]


def test_rejudge_refuses_a_final_run(tmp_path: Path):
    blind = [question("B1", blind=True)]
    source = run_batch(blind, tmp_path, FakeBatchClient(), final=True)
    client = FakeBatchClient()

    with pytest.raises(ValueError, match="final run"):
        rejudge(source, blind, tmp_path, client)
    assert client.batches.created == []


def test_rejudge_refuses_a_folder_with_no_run(tmp_path: Path):
    with pytest.raises(ValueError, match="run.json"):
        rejudge(tmp_path, [question("Q1")], tmp_path, FakeBatchClient())


def test_rejudge_over_the_cost_cap_sends_nothing(tmp_path: Path):
    questions = [question("Q1")]
    source = run_batch(questions, tmp_path, FakeBatchClient())
    client = FakeBatchClient()

    run_dir = rejudge(source, questions, tmp_path, client, max_cost=0.000001)

    assert client.batches.created == []
    meta = read(run_dir / "run.json")
    assert meta["stopped_early"] is True
    assert meta["stop_reason"] == "batch_estimate"


def test_rejudge_keeps_the_source_runs_selection_and_answer_errors(tmp_path: Path):
    questions = [question("Q1"), question("Q2"), question("Q3")]
    source = run_batch(questions, tmp_path, FakeBatchClient(), )
    meta_path = source / "run.json"
    meta = read(meta_path)
    # As if the source run was `--only Q1,Q2` and Q2's answer failed.
    meta["question_ids"] = ["Q1", "Q2"]
    meta["errors"] = [{"id": "Q2", "error": "RuntimeError: synthetic failure"}]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    (source / "answers" / "Q2.json").unlink()
    client = FakeBatchClient()

    run_dir = rejudge(source, questions, tmp_path, client)

    rejudged = read(run_dir / "run.json")
    assert rejudged["question_ids"] == ["Q1", "Q2"]
    assert rejudged["asked_ids"] == ["Q1", "Q2"]
    assert rejudged["errors"] == [{"id": "Q2", "error": "RuntimeError: synthetic failure"}]
    [judging] = client.batches.created
    assert [r["custom_id"] for r in judging] == ["Q1-0"]


def test_rejudge_refuses_an_unreadable_run_file(tmp_path: Path):
    (tmp_path / "run.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="run.json"):
        rejudge(tmp_path, [question("Q1")], tmp_path, FakeBatchClient())


def test_rejudge_refuses_a_run_file_with_no_model(tmp_path: Path):
    (tmp_path / "run.json").write_text(json.dumps({"final": False}), encoding="utf-8")

    with pytest.raises(ValueError, match="run.json"):
        rejudge(tmp_path, [question("Q1")], tmp_path, FakeBatchClient())


def test_rejudge_keeps_an_empty_asked_list(tmp_path: Path):
    questions = [question("Q1")]
    source = run_batch(questions, tmp_path, FakeBatchClient())
    meta = read(source / "run.json")
    meta["asked_ids"] = []
    (source / "run.json").write_text(json.dumps(meta), encoding="utf-8")

    run_dir = rejudge(source, questions, tmp_path, FakeBatchClient())

    assert read(run_dir / "run.json")["asked_ids"] == []

