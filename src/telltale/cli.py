"""Command line: `telltale ingest` builds the store and search index; `telltale ask` answers;
`telltale serve` runs the local web page; `telltale eval` scores answers against the private
question set; `telltale eval-report` rebuilds a run's report from its saved files."""

import argparse
import csv
import json
import logging
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import anthropic
import httpx
import uvicorn

from telltale.answer import (
    SEARCHED_CLOSING,
    answer,
    is_no_answer,
    is_no_answer_result,
    reports_searched,
    stop_notice,
)
from telltale import card as answer_card
from telltale.departments import RULES, departments
from telltale.evals.judge import UnsupportedJudgeModel, judge_answer, require_judge_model
from telltale.evals.questions import (
    BlindQuestionError,
    EvalQuestion,
    QuestionFileError,
    load_questions,
    select,
)
from telltale.evals.report import REVIEW_FILE, ReportError, run_totals, write_report
from telltale.evals.run import RUN_FILE, DirtyTreeError, RunHalted, rejudge_run, run_eval
from telltale.http import describe
from telltale.ingest.download import DownloadError, download_pdf
from telltale.ingest.extract import group_by_page, read_pages, to_passages
from telltale.ingest.ocr import LocalOcr, PageReader
from telltale.ingest.store import Store
from telltale.ingest.webpage import page_passages
from telltale.models import Answer, ReportMeta
from telltale.pricing import UnknownModelPrice, require_price
from telltale.question import question_problem
from telltale.search import EmbedderUnavailable, FastEmbedder, HybridIndex, SearchIndexError
from telltale.settings import DEFAULT_JUDGE_MODEL, Settings, SettingsError, load_settings
from telltale.sources.maib import ListingError, SkippedReport, list_merchant_reports
from telltale.web import create_app

STORE_FILE = "telltale.sqlite"
PDF_DIR = "pdf"
INDEX_DIR = "index"
MODEL_DIR = "models"
DEPARTMENTS_FILE = "departments.csv"
OCR_DIR = "ocr"
HTTP_TIMEOUT_SECONDS = 30.0
CROWN_COPYRIGHT = "Crown copyright"
RUN_INGEST = "Run `telltale ingest` first."
MODEL_OFFLINE = (
    "The search model is not available offline. Run `telltale ingest` once with internet access."
)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
# The private eval question set, read from the working directory (the repo root).
QUESTIONS_FILE = Path("docs/private/eval/questions.json")
EVAL_RUNS_DIR = Path("eval") / "runs"
FINAL_RUNS_LOG = Path("eval") / "final-runs.jsonl"
DEFAULT_MAX_COST_USD = 1.00
EVAL_STOPPED_BEFORE_START = "Stopped before the run started; nothing was asked."
EVAL_STOPPED = (
    "Stopped. Anything asked so far is saved in the run folder named above. A re-run starts a "
    "new run."
)
EVAL_STOPPED_FINAL = (
    "Stopped. Anything asked so far is saved in the run folder named above, and the run is "
    "already in the final-run log. A re-run starts a new run."
)
EVAL_BATCH_STOPPED = (
    "A batch already sent keeps running and is still charged; its id is in run.json."
)

logger = logging.getLogger(__name__)


class CommandError(Exception):
    """A problem to report in a line or two, without a traceback."""


class EvalStopped(Exception):
    """`telltale eval` was stopped with Ctrl+C after its run began; the message says what is
    saved."""


def _groups(n: int) -> str:
    return f"{n:,} page group" + ("" if n == 1 else "s")


def _why(error: Exception) -> str:
    return describe(error) if isinstance(error, httpx.HTTPError) else str(error)


class ReportFailed(Exception):
    """One report's PDF could not be downloaded or read; the run carries on."""


def _store_text(
    client: httpx.Client,
    store: Store,
    meta: ReportMeta,
    pdf_dir: Path,
    ocr: PageReader | None = None,
    ocr_dir: Path | None = None,
) -> str:
    """Download and extract one report's PDF into the store, and say what happened. A report
    with no PDF is stored from the text of its GOV.UK page instead.

    Raises `ReportFailed` on a download or PDF error, which the caller records and passes over.
    """
    try:
        path = download_pdf(client, meta, pdf_dir)
    except (httpx.HTTPError, DownloadError, OSError) as error:
        raise ReportFailed(f"download failed: {_why(error)}") from error
    if path is None:
        passages = page_passages(meta.page_html or "")
        groups = group_by_page([(1, passage) for passage in passages])
        store.replace_chunks(meta.slug, groups)
        if not groups:
            return "no PDF link and no page text"
        return f"no PDF, {_groups(len(groups))} from the GOV.UK page"
    try:
        pages = read_pages(path, ocr=ocr, cache_dir=ocr_dir)
    except Exception as error:  # pypdf raises many kinds of error on a damaged file
        raise ReportFailed(f"PDF could not be read: {type(error).__name__}: {error}") from error
    groups = group_by_page(to_passages([text for text, _ in pages]))
    ocr_pages = frozenset(number for number, (_, by_ocr) in enumerate(pages, start=1) if by_ocr)
    store.replace_chunks(meta.slug, groups, ocr_pages)
    note = f", {len(ocr_pages)} page{'' if len(ocr_pages) == 1 else 's'} read by OCR" if ocr_pages else ""
    return _groups(len(groups)) + note


def _print_reports(heading: str, reports: list[tuple[str, str, str]]) -> None:
    """A heading with a count, then each report's title (and note) and page link."""
    print(f"\n{heading} ({len(reports)}):")
    for title, note, link in reports:
        print(f"  - {title}{': ' + note if note else ''}")
        print(f"    {link or 'no page link'}")


def page_reader() -> PageReader:
    """OCR for scanned pages. Tests replace this so no OCR model is loaded."""
    return LocalOcr()


def embedder(data_dir: Path) -> FastEmbedder:
    """The local embedding model, kept in the data folder so it survives a reboot offline."""
    return FastEmbedder(cache_dir=data_dir / MODEL_DIR)


def ingest(data_dir: Path, limit: int | None) -> None:
    """List, download, extract and store MAIB reports, rebuild the search index, report."""
    pdf_dir, index_dir, ocr_dir = data_dir / PDF_DIR, data_dir / INDEX_DIR, data_dir / OCR_DIR
    ocr = page_reader()
    skipped: list[SkippedReport] = []
    failed: list[tuple[ReportMeta, str]] = []

    with (
        httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client,
        Store(data_dir / STORE_FILE) as store,
    ):
        print("Listing MAIB reports (one request per report; this takes a while).")
        try:
            reports = list_merchant_reports(client, limit=limit, on_skip=skipped.append)
        except (httpx.HTTPError, ListingError) as error:
            raise CommandError(f"Could not list MAIB reports: {_why(error)}") from error
        print(f"Listed {len(reports)} reports.")

        for n, meta in enumerate(reports, start=1):
            store.upsert_report(meta)
            try:
                outcome = _store_text(client, store, meta, pdf_dir, ocr, ocr_dir)
            except ReportFailed as error:
                failed.append((meta, str(error)))
                logger.warning("%s: %s", meta.slug, error)
                outcome = "FAILED, listed under errors below"
            print(f"[{n}/{len(reports)}] {meta.slug}: {outcome}")

        # Always rebuilt from the whole store, never loaded (Ruling 12). A failure is reported
        # after the lists below, so they are never lost to it.
        chunks = store.chunks()
        print(f"Building the search index over {_groups(len(chunks))}.")
        index_error: Exception | None = None
        try:
            HybridIndex.build(chunks, embedder(data_dir)).save(index_dir)
        except Exception as error:
            index_error = error
            if not isinstance(error, EmbedderUnavailable):
                # Not the expected offline case, so keep the traceback for a bug report.
                logger.warning("The search index build failed", exc_info=error)

        counts = store.counts()
        print(f"\nStore {data_dir / STORE_FILE}, all runs so far:")
        print(f"  Reports:                 {counts['reports']:,}")
        for kind, count in store.report_types().items():
            print(f"    {kind}: {count:,}")
        print(f"  Reports with a PDF link: {counts['with_pdf']:,}")
        print(f"  Reports read from their GOV.UK page (no PDF): {counts['from_page']:,}")
        print(f"  Page groups (chunks):    {counts['chunks']:,}")
        print(f"  Passages:                {counts['passages']:,}")
        print(f"  Pages read by OCR:       {store.ocr_pages():,}")
        if index_error is None:
            print(f"Search index saved to {index_dir}")

        _print_reports(
            "Reports with no PDF link and no page text, not searchable",
            [(m.title, "", m.page_url) for m in store.missing_pdf()],
        )
        _print_reports(
            "Reports with a PDF link but no text (download failed, unreadable or image-only PDF), "
            "not searchable",
            [(m.title, "", m.page_url) for m in store.without_text()],
        )

    _print_reports("Errors this run", [(m.title, why, m.page_url) for m, why in failed])
    dropped = [s for s in skipped if s.dropped]
    _print_reports(
        "Left out of the listing because they could not be read",
        [(s.title, s.reason, s.page_url) for s in dropped],
    )
    others = Counter(s.reason for s in skipped if not s.dropped)
    detail = ", ".join(f"{kind} {count}" for kind, count in sorted(others.items()))
    print(
        f"\nOther report types left out: {others.total()}"
        + (f" ({detail})" if detail else "")
    )

    if isinstance(index_error, EmbedderUnavailable):
        raise CommandError(
            f"\nThe search index was not built: {index_error}. The reports above are stored; "
            "run `telltale ingest` again with internet access to build it."
        ) from index_error
    if index_error is not None:
        raise CommandError(
            f"\nThe search index was not built: {type(index_error).__name__}: {index_error}. "
            "The reports above are stored; run `telltale ingest` again to build it."
        ) from index_error


def load_index(data_dir: Path) -> HybridIndex:
    """The saved search index over the stored chunks; `CommandError` if missing or stale."""
    store_path = data_dir / STORE_FILE
    if not store_path.is_file():
        raise CommandError(f"No report store at {store_path}. {RUN_INGEST}")
    with Store(store_path) as store:
        chunks = store.chunks()
    try:
        return HybridIndex.load(data_dir / INDEX_DIR, chunks, embedder(data_dir))
    except SearchIndexError as error:
        raise CommandError(
            f"The search index is missing or out of date: {error}. {RUN_INGEST}"
        ) from error


def print_answer(result: Answer) -> None:
    """Numbered sentences (`[no source]` on unsourced ones), numbered sources, then the cost.

    A no-answer result also lists the first reports searched, so it can be checked.
    """
    numbers: dict[str, int] = {}
    titles: dict[str, str] = {}
    pages: dict[str, int | None] = {}
    notice, notice_only = stop_notice(result)

    if notice_only:
        print(notice)
    else:
        n = 0
        for sentence in result.sentences:
            if is_no_answer(sentence):
                print(sentence.text)
                continue
            refs: list[int] = []
            for citation in sentence.citations:
                number = numbers.setdefault(citation.source, len(numbers) + 1)
                titles.setdefault(citation.source, citation.report_title)
                pages.setdefault(citation.source, citation.page)
                if number not in refs:
                    refs.append(number)
            n += 1
            mark = "".join(f"[{number}]" for number in refs) if refs else "[no source]"
            print(f"{n}. {sentence.text} {mark}")
        if notice:
            print(f"\n{notice}")
        searched = reports_searched(result.chunks) if is_no_answer_result(result) else []
        if searched:
            print("\nReports searched (closest first):")
            for chunk in searched:
                where = f", page {chunk.source_page}" if chunk.source_page is not None else ""
                print(f"- {chunk.report_title}{where}: {chunk.source}")
            print(SEARCHED_CLOSING)

    if numbers:
        print("\nSources:")
        for source, number in numbers.items():
            where = f", page {pages[source]}" if pages[source] is not None else ""
            print(f"[{number}] {titles[source]}{where}. {CROWN_COPYRIGHT}.")
            print(f"    {source}")

    print(
        f"\nCost: ${result.cost_usd:.4f} "
        f"({result.input_tokens:,} in / {result.output_tokens:,} out)"
    )


def _settings_with_key(command: str) -> Settings:
    """Settings with the API key, which `ask` and `serve` need (Ruling 16)."""
    try:
        return load_settings()
    except SettingsError as error:
        raise CommandError(
            f"{error}. `telltale {command}` needs it; `telltale ingest` does not."
        ) from error


def ask(
    question: str,
    save: Path | None = None,
    card_out: Path | None = None,
    sentences: str | None = None,
) -> None:
    # The same checks as the page, before any key, index or model is touched.
    problem = question_problem(question)
    if problem is not None:
        raise CommandError(problem)
    if sentences is not None and card_out is None:
        raise CommandError("--sentences only applies with --card.")
    question = question.strip()
    settings = _settings_with_key("ask")
    index = load_index(settings.data_dir)
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        result = answer(question, index, client, settings.model)
    except EmbedderUnavailable as error:
        raise CommandError(MODEL_OFFLINE) from error
    except anthropic.APIError as error:
        raise CommandError(f"The request to Claude failed: {error}") from error
    print_answer(result)
    if save is not None:
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_text(answer_card.to_json(result), encoding="utf-8")
        print(f"\nAnswer saved to {save}")
    if card_out is not None:
        make_card(result, card_out, sentences)


def make_card(
    result: Answer,
    out: Path,
    sentences: str | None,
    searched: list[answer_card.Searched] | None = None,
) -> None:
    """Draw the answer card and say where it went.

    `searched` is the reports searched from a saved answer; without it the card uses those of
    `result.chunks`.
    """
    highest = sum(1 for line in answer_card.view(result).lines if line.number is not None)
    try:
        selection = answer_card.parse_selection(sentences, highest)
        answer_card.render(result, out, selection, searched=searched)
    except answer_card.CardError as error:
        raise CommandError(str(error)) from error
    print(f"Card saved to {out}")


def card(saved: Path, out: Path, sentences: str | None) -> None:
    """Redraw a card from an answer saved with `ask --save`, without asking again."""
    try:
        text = saved.read_text(encoding="utf-8")
        result = answer_card.from_json(text)
        searched = answer_card.searched_from_json(text)
    except OSError as error:
        raise CommandError(f"Could not read {saved}: {error.strerror}") from error
    except (ValueError, KeyError, TypeError) as error:
        raise CommandError(f"{saved} is not an answer saved by `telltale ask --save`.") from error
    make_card(result, out, sentences, searched)


def serve(host: str, port: int) -> None:
    """Load the store, index and embedding model once, then run the web page until stopped."""
    settings = _settings_with_key("serve")
    index = load_index(settings.data_dir)
    try:
        # Loads the embedding model now, so an offline laptop fails here, not on the first question.
        index.search("start-up check", k=1)
    except EmbedderUnavailable as error:
        raise CommandError(MODEL_OFFLINE) from error
    except SearchIndexError as error:
        raise CommandError(
            f"The search index is missing or out of date: {error}. {RUN_INGEST}"
        ) from error
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    app = create_app(lambda question: answer(question, index, client, settings.model))
    print(f"Telltale is running at http://{host}:{port}/ (press Ctrl+C to stop).")
    uvicorn.run(app, host=host, port=port, log_level="warning")


def _git_state() -> tuple[str, bool]:
    """The short commit hash and whether the tree has uncommitted changes.

    Outside a git checkout the commit is "unknown" and the tree counts as dirty, so a final run
    refuses rather than record results that belong to no commit.
    """
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True
    return commit or "unknown", bool(status.strip())


def _only(value: str | None) -> set[str] | None:
    """The ids named by `--only`, or None when it was not given (None means every question)."""
    if value is None:
        return None
    ids = {part.strip() for part in value.split(",") if part.strip()}
    if not ids:
        raise CommandError("--only needs at least one question id, for example --only Q1,Q2.")
    return ids


def _load_eval_questions(data_dir: Path) -> list[EvalQuestion]:
    """Every question in the private question file, checked against the report store."""
    if not QUESTIONS_FILE.is_file():
        raise CommandError(
            f"No eval question file at {QUESTIONS_FILE}. Put the private question set there "
            "and run from the repo root."
        )
    store_path = data_dir / STORE_FILE
    if not store_path.is_file():
        raise CommandError(f"No report store at {store_path}. {RUN_INGEST}")
    with Store(store_path) as store:
        known_slugs = {meta.slug for meta in store.reports()}
    return load_questions(QUESTIONS_FILE, known_slugs)


def _write_run_report(run_dir: Path, questions: list[EvalQuestion]) -> None:
    try:
        report = write_report(run_dir, questions)
    except ReportError as error:
        raise CommandError(str(error)) from error
    except OSError as error:
        raise CommandError(f"Could not write the report: {error}") from error
    print(f"Report written to {report}")
    print(f"Judge review sheet: {run_dir / REVIEW_FILE}")


def evaluate_report(run_dir: Path) -> None:
    """Rebuild a run's report and judge review sheet from its saved files. No key, no cost."""
    if not (run_dir / RUN_FILE).is_file():
        raise CommandError(f"No {RUN_FILE} in {run_dir}; give a folder made by `telltale eval`.")
    try:
        questions = _load_eval_questions(load_settings(require_key=False).data_dir)
    except QuestionFileError as error:
        raise CommandError(str(error)) from error
    _write_run_report(run_dir, questions)


def evaluate_rejudge(source_dir: Path, judge_model: str, max_cost: float) -> None:
    """Judge a working run's saved answers again, as one batch, in a new run folder, then write
    its report. No answer is asked again. Refusals come before the key is read."""
    meta_path = source_dir / RUN_FILE
    if not meta_path.is_file():
        raise CommandError(
            f"No {RUN_FILE} in {source_dir}; give a folder made by `telltale eval`."
        )
    try:
        source_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:  # ValueError covers bad JSON and bad UTF-8
        raise CommandError(f"The {RUN_FILE} in {source_dir} cannot be read: {error}") from error
    if not isinstance(source_meta, dict) or "model" not in source_meta:
        raise CommandError(f"The {RUN_FILE} in {source_dir} is not a run file.")
    if source_meta.get("final"):
        raise CommandError(
            "That is a final run; its blind answers are judged once, so it is not re-judged."
        )
    try:
        require_judge_model(judge_model)
    except (UnknownModelPrice, UnsupportedJudgeModel) as error:
        raise CommandError(str(error)) from error
    data_dir = load_settings(require_key=False).data_dir
    try:
        questions = select(_load_eval_questions(data_dir), final=False)
    except (QuestionFileError, BlindQuestionError) as error:
        raise CommandError(str(error)) from error
    commit, dirty = _git_state()

    settings = _settings_with_key("eval-rejudge")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    print(
        f"Re-judging the saved answers in {source_dir} with {judge_model}, as one batch at "
        "half the live price. No answer is asked again."
    )
    sent = {"batch": False}

    def batch_sent(kind: str, batch_id: str) -> None:
        sent["batch"] = True
        print(f"The {kind} batch {batch_id} is sent; waiting for it to end.", flush=True)

    try:
        run_dir = rejudge_run(
            source_dir, questions, settings.data_dir / EVAL_RUNS_DIR, client, max_cost,
            judge_model=judge_model, commit=commit, dirty=dirty,
            on_start=lambda d: print(f"Saving the re-judged run to {d}", flush=True),
            on_batch=batch_sent,
        )
    except RunHalted as halted:
        raise CommandError(f"{halted} The run so far is in {halted.run_dir}") from halted
    except ValueError as error:
        raise CommandError(str(error)) from error
    except OSError as error:
        raise CommandError(f"Could not write the run: {error}") from error
    except KeyboardInterrupt:
        lines = [EVAL_STOPPED] + ([EVAL_BATCH_STOPPED] if sent["batch"] else [])
        raise EvalStopped("\n".join(lines)) from None

    meta = json.loads((run_dir / RUN_FILE).read_text(encoding="utf-8"))
    print(f"Cost: ${meta['judge_cost_usd']:.4f} (judging only)")
    if meta["stopped_early"]:
        print(
            f"Stopped early: the estimate for the judge batch would pass the cost cap of "
            f"${max_cost:.2f}, so it was not sent."
        )
    for problem in meta["errors"]:
        print(f"{problem['id']} failed: {problem['error']}")
    print(f"Run saved to {run_dir}")
    _write_run_report(run_dir, questions)


def evaluate(
    final: bool,
    only: str | None,
    model: str | None,
    judge_model: str,
    max_cost: float,
    batch: bool = False,
) -> None:
    """Ask the working (or with `final` the blind) questions, judge each answer and save the run.

    With `batch`, the answers and then the judge calls go as two Message Batches, at half the
    live price; the run waits for each to end.

    Every refusal (a model with no price, no question file, an unknown or blind id, a dirty tree
    for a final run) comes before the key, the search index or any model is touched.
    """
    settings = load_settings(require_key=False)
    data_dir = settings.data_dir
    answer_model = model or settings.model
    try:
        require_price(answer_model)
        require_judge_model(judge_model)
    except (UnknownModelPrice, UnsupportedJudgeModel) as error:
        raise CommandError(str(error)) from error
    try:
        questions = select(_load_eval_questions(data_dir), final=final, only=_only(only))
    except (QuestionFileError, BlindQuestionError) as error:
        raise CommandError(str(error)) from error
    if not questions:
        kind = "blind" if final else "working"
        raise CommandError(f"The question file has no {kind} questions to run.")
    commit, dirty = _git_state()
    if final and dirty:
        raise CommandError(
            "A final run needs every change committed first; the tree has uncommitted changes."
        )

    settings = _settings_with_key("eval")
    index = load_index(settings.data_dir)
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    final_log = settings.data_dir / FINAL_RUNS_LOG
    print(f"Asking {len(questions)} question(s) with {answer_model}, judged by {judge_model}.")
    if batch:
        print(
            "Batch mode: the answers, then the judge calls, go as two batches at half the live "
            "price. Each batch can take up to 24 hours."
        )

    progress = {"started": False, "batch_sent": False}

    def started(run_dir: Path) -> None:
        progress["started"] = True
        if final:
            print(f"Final run logged in {final_log}")
        print(f"Saving the run to {run_dir}", flush=True)

    def batch_sent(kind: str, batch_id: str) -> None:
        progress["batch_sent"] = True
        print(f"The {kind} batch {batch_id} is sent; waiting for it to end.", flush=True)

    try:
        _run_and_report(
            questions, index, client, answer_model, judge_model, max_cost, final, commit, dirty,
            final_log, batch, settings.data_dir / EVAL_RUNS_DIR, started, batch_sent,
        )
    except KeyboardInterrupt:
        if not progress["started"]:
            raise
        lines = [EVAL_STOPPED_FINAL if final else EVAL_STOPPED]
        if progress["batch_sent"]:
            lines.append(EVAL_BATCH_STOPPED)
        raise EvalStopped("\n".join(lines)) from None


def _run_and_report(
    questions: list[EvalQuestion],
    index,
    client,
    answer_model: str,
    judge_model: str,
    max_cost: float,
    final: bool,
    commit: str,
    dirty: bool,
    final_log: Path,
    batch: bool,
    runs_dir: Path,
    started: Callable[[Path], None],
    batch_sent: Callable[[str, str], None],
) -> None:
    """The run itself, then its cost, unjudged count, errors and report."""
    try:
        run_dir = run_eval(
            questions,
            lambda text: answer(text, index, client, answer_model),
            lambda saved: judge_answer(saved, client, judge_model),
            runs_dir,
            max_cost,
            model=answer_model,
            judge_model=judge_model,
            final=final,
            commit=commit,
            dirty=dirty,
            final_log=final_log,
            on_start=started,
            batch=batch,
            index=index,
            client=client,
            on_batch=batch_sent,
        )
    except RunHalted as halted:
        raise CommandError(f"{halted} The run so far is in {halted.run_dir}") from halted
    except (DirtyTreeError, ValueError) as error:
        raise CommandError(str(error)) from error
    except OSError as error:
        raise CommandError(f"Could not write the run: {error}") from error

    meta = json.loads((run_dir / RUN_FILE).read_text(encoding="utf-8"))
    spent = meta["answer_cost_usd"] + meta["judge_cost_usd"]
    print(
        f"Cost: ${spent:.4f} (answers ${meta['answer_cost_usd']:.4f}, "
        f"judging ${meta['judge_cost_usd']:.4f})"
    )
    try:
        run = run_totals(run_dir, questions)
    except ReportError:
        pass  # the report below says what is wrong with the run's files
    else:
        print(f"Unjudged: {run.unjudged} of {run.cited} cited sentences")
    if meta["stopped_early"] and batch:
        print(
            f"Stopped early: the estimate for the next batch would pass the cost cap of "
            f"${max_cost:.2f}, so it was not sent."
        )
    elif meta["stopped_early"]:
        print(f"Stopped early: the cost cap of ${max_cost:.2f} was reached.")
    for problem in meta["errors"]:
        print(f"{problem['id']} failed: {problem['error']}")
    print(f"Run saved to {run_dir}")
    _write_run_report(run_dir, questions)
    failed = {problem["id"] for problem in meta["errors"]}
    if failed and set(meta["asked_ids"]) <= failed:
        raise CommandError("Every question asked failed; nothing was scored.")


def sort_by_department(data_dir: Path, show_list: bool) -> None:
    """Count the stored reports by department, from their titles, and write a CSV to check."""
    store_path = data_dir / STORE_FILE
    if not store_path.is_file():
        raise CommandError(f"No report store at {store_path}. {RUN_INGEST}")
    with Store(store_path) as store:
        reports = store.reports()

    rows = []
    for meta in reports:
        placements = departments(meta.title)
        rows.append((meta, placements))

    unsorted = [meta for meta, placements in rows if not placements]
    several = sum(1 for _, placements in rows if len(placements) > 1)
    print(f"{len(reports):,} reports, sorted from the words in their titles:")
    for department in RULES:
        count = sum(1 for _, ps in rows if any(p.department == department for p in ps))
        print(f"  {department}: {count:,}")
    print(f"  In more than one department: {several:,}")
    print(f"  Not sorted (title too general): {len(unsorted):,}")

    out = data_dir / DEPARTMENTS_FILE
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "report type", "departments", "matched words", "page link"])
        for meta, placements in rows:
            writer.writerow([
                meta.title,
                meta.report_type,
                "; ".join(p.department for p in placements) or "Not sorted",
                "; ".join(f"{p.department}: {', '.join(p.matched)}" for p in placements),
                meta.page_url,
            ])
    print(f"Every report, with the words that placed it: {out}")

    if show_list:
        for department in [*RULES, "Not sorted"]:
            titles = [
                meta.title
                for meta, ps in rows
                if (department == "Not sorted" and not ps)
                or any(p.department == department for p in ps)
            ]
            print(f"\n{department} ({len(titles)}):")
            for title in titles:
                print(f"  - {title}")


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


def _port(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return number


def _money(value: str) -> float:
    number = float(value)
    if not number > 0:
        raise argparse.ArgumentTypeError("must be more than 0")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telltale",
        description="Briefs ship's officers from published accident reports, every sentence cited.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_parser = commands.add_parser(
        "ingest",
        help="download MAIB reports and build the search index",
        description="List MAIB merchant-ship reports, download and extract their "
        "PDFs into the store, rebuild the search index and list any report left unsearchable. "
        "Safe to re-run: PDFs already downloaded are kept. Needs no API key.",
    )
    ingest_parser.add_argument(
        "--limit", type=_positive, metavar="N", help="stop after N reports"
    )
    ask_parser = commands.add_parser(
        "ask",
        help="ask what has gone wrong on other ships during a job",
        description="Answer from the stored reports, every sentence linked to its source. "
        "Needs ANTHROPIC_API_KEY and a finished `telltale ingest`.",
    )
    ask_parser.add_argument("question", help="the job the ship is about to do, in plain words")
    ask_parser.add_argument("--save", type=Path, help="also save the answer as JSON, to redraw cards later")
    ask_parser.add_argument("--card", type=Path, help="also draw the answer as a phone-sized PNG card")
    ask_parser.add_argument(
        "--sentences", help='with --card: which sentences to put on it, for example "1-4,7"'
    )
    card_parser = commands.add_parser(
        "card",
        help="draw an answer saved with `ask --save` as a phone-sized PNG card",
        description="Redraw a card from a saved answer without asking again (no API key, no cost). "
        "Every sentence is shown word for word with its source numbers or no-source mark.",
    )
    card_parser.add_argument("saved", type=Path, help="the JSON file written by `ask --save`")
    card_parser.add_argument("--out", type=Path, required=True, help="where to write the PNG")
    card_parser.add_argument("--sentences", help='which sentences to show, for example "1-4,7"')
    departments_parser = commands.add_parser(
        "departments",
        help="count the stored reports by shipboard department",
        description="Sort the stored reports into Bridge and navigation, Deck, Engine and "
        "Catering and accommodation from the words in their titles, print the counts and "
        "write every report with the words that placed it to data/departments.csv for "
        "checking. Needs a finished `telltale ingest`; no API key.",
    )
    departments_parser.add_argument(
        "--list", action="store_true", help="also print every title under its department"
    )
    serve_parser = commands.add_parser(
        "serve",
        help="run the local web page",
        description="Run the question page in your browser: each sentence links to its "
        "sources, or is marked when it has none. Needs ANTHROPIC_API_KEY and a finished "
        "`telltale ingest`.",
    )
    serve_parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"address to listen on (default {DEFAULT_HOST}, this computer only). The page "
        "has no login and every question spends API credit, so keep 127.0.0.1 unless the "
        "network is trusted",
    )
    serve_parser.add_argument(
        "--port", type=_port, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})"
    )
    eval_parser = commands.add_parser(
        "eval",
        help="ask the eval questions and judge every cited sentence",
        description=f"Ask each question in {QUESTIONS_FILE} once, judge each cited sentence "
        "against the passages it cites, and save the answers, verdicts and costs under "
        "data/eval/runs/. Runs the working questions; --final runs the blind ones, needs a "
        "committed tree and is logged. Needs ANTHROPIC_API_KEY and a finished `telltale ingest`.",
    )
    eval_parser.add_argument(
        "--final", action="store_true", help="run the blind questions (committed tree only)"
    )
    eval_parser.add_argument("--only", help="run only these question ids, for example Q1,Q2")
    eval_parser.add_argument("--model", help="the answering model (default: the ask model)")
    eval_parser.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        help=f"the judging model (default {DEFAULT_JUDGE_MODEL})",
    )
    eval_parser.add_argument(
        "--max-cost",
        type=_money,
        default=DEFAULT_MAX_COST_USD,
        metavar="USD",
        help=f"stop before the next question once this much is spent "
        f"(default {DEFAULT_MAX_COST_USD:.2f})",
    )
    eval_parser.add_argument(
        "--batch",
        action="store_true",
        help="send the answers, then the judge calls, as two batches at half the live price; "
        "each can take up to 24 hours, and a batch is not sent if its estimated cost would "
        "pass the cost cap",
    )
    report_parser = commands.add_parser(
        "eval-report",
        help="rebuild an eval run's report and judge review sheet",
        description="Read a run folder written by `telltale eval` and write report.md and "
        "judge-review.csv in it, from the saved answers and verdicts only. Needs the "
        "question file and a finished `telltale ingest`; no API key, no cost.",
    )
    report_parser.add_argument("run_dir", type=Path, help="the run folder, under data/eval/runs/")
    rejudge_parser = commands.add_parser(
        "eval-rejudge",
        help="judge a working run's saved answers again, without asking again",
        description="Copy the saved answers of a working run folder into a new run folder and "
        "judge every cited sentence again, as one batch at half the live price, then write its "
        "report and judge review sheet. No answer is asked again, and a final run is refused. "
        "Needs ANTHROPIC_API_KEY.",
    )
    rejudge_parser.add_argument(
        "run_dir", type=Path, help="the run folder to re-judge, under data/eval/runs/"
    )
    rejudge_parser.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        help=f"the judging model (default {DEFAULT_JUDGE_MODEL})",
    )
    rejudge_parser.add_argument(
        "--max-cost",
        type=_money,
        default=DEFAULT_MAX_COST_USD,
        metavar="USD",
        help="do not send the judge batch if its estimated cost would pass this "
        f"(default {DEFAULT_MAX_COST_USD:.2f})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    try:
        if args.command == "ingest":
            ingest(load_settings(require_key=False).data_dir, args.limit)
        elif args.command == "ask":
            ask(args.question, args.save, args.card, args.sentences)
        elif args.command == "card":
            card(args.saved, args.out, args.sentences)
        elif args.command == "departments":
            sort_by_department(load_settings(require_key=False).data_dir, args.list)
        elif args.command == "eval":
            evaluate(
                args.final, args.only, args.model, args.judge_model, args.max_cost, args.batch
            )
        elif args.command == "eval-report":
            evaluate_report(args.run_dir)
        elif args.command == "eval-rejudge":
            evaluate_rejudge(args.run_dir, args.judge_model, args.max_cost)
        else:
            serve(args.host, args.port)
    except CommandError as error:
        print(error, file=sys.stderr)
        return 1
    except EvalStopped as stopped:
        print(stopped, file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        if args.command in ("eval", "eval-rejudge"):
            print(EVAL_STOPPED_BEFORE_START, file=sys.stderr)
        else:
            print("Stopped. Re-run to carry on; nothing is downloaded twice.", file=sys.stderr)
        return 130
    return 0
