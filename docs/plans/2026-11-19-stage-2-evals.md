# Stage 2: Evals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Score the stage 1 system on Varun's 50-question test set: right reports found, right "no reliable answer", and the share of answer sentences not backed by the passage they cite (headline, target zero).

**Architecture:** A new package `telltale.evals` reads a private question file, asks each working question through the existing `answer()`, saves every answer in the existing `--save` JSON shape, has a judge model check each cited sentence against its cited passages, and writes a run report. Scoring reads only the saved files, so a report can be rebuilt without paying again. Blind questions run only with `--final`, from a clean commit, and every final run is logged.

**Tech Stack:** Python 3.12 via uv, the `anthropic` SDK already in use, pytest, the existing SQLite store and `HybridIndex`.

**Spec:** `docs/specs/2026-10-30-design.md` (sections 5, 8, 10, 10a). Question set and picking rule: `docs/private/test-questions.md` (private). Rulings: `docs/plans/stage-1-handover/ledger.md` (continue numbering from Ruling 33).

## Global Constraints

- Rule 0 (spec section 5): "Never invent a fact." The headline measure is "the share of sentences not backed by their cited passage; the target is zero" (spec section 8, stage 2).
- Scoring covers "right reports found" and "answer agrees with Varun's answer" (spec section 10). Varun's answer for each question is a list of expected reports, or "no reliable answer".
- "A held-out part is kept from the start and is never used for training" (spec section 10). The blind ids are listed only in `docs/private/test-questions.md` ("Blind set, final"). They are never run while anything is being tuned, and never appear in a prompt used for tuning, a post, a reply, this plan, `src/`, `tests/` or the public repo.
- The question file lives only under `docs/private/` (never in the public snapshot). Run outputs live under `data/` (git-ignored). Tests use synthetic questions only: no real test question text, id list or expected report in `src/` or `tests/`.
- Answering model stays `claude-haiku-5-5` (`settings.DEFAULT_MODEL`); "move to Sonnet 5.5 or Opus 5.5 only where the eval set shows a better score" (spec 10a). Budget: "$150 ceiling for the three months"; "Eval runs use the batch discount" (spec 10a; Task 6).
- Never read, print or log `.env*` or the key. Every subagent dispatch says so.
- Never push. Private branch only; the public repo only via the snapshot, with Varun's go-ahead.
- Writing in docs and output: plain English, no em dashes.

## Review Focus

1. **A blind question run by accident** (no `--final`, or `--only` naming a blind id): the run must refuse and name the flag, before any model call.
2. **An expected report slug that is not in the store** (typo, or a report re-ingested under a new slug): loading must fail listing every bad slug, not score it as "not found".
3. **A citation to a report read from its GOV.UK page or by OCR**: the cited report must still map to its slug (its sources differ from the PDF form).
4. **A refused, cut-off or no-results answer**: scoring must not crash; a refusal on a report question is a miss, and a cut-off answer still has its sentences judged.
5. **The judge failing or replying badly for one sentence**: that sentence is counted as "unjudged" and reported, never counted as supported, and the run goes on.

Each has a test in the owning task below.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/telltale/evals/__init__.py` | Public names of the package |
| `src/telltale/evals/questions.py` | Question file shape, loading, validation, working/blind selection |
| `src/telltale/evals/score.py` | Pure scoring of saved answers and verdicts |
| `src/telltale/evals/judge.py` | One judge call per cited sentence; verdict parsing |
| `src/telltale/evals/run.py` | A run: guards, asking, saving, judging, cost cap, final-run log |
| `src/telltale/evals/report.py` | Run report (markdown) and Varun's judge review sheet (CSV) |
| `src/telltale/cli.py` | `telltale eval` and `telltale eval-report` commands |
| `docs/private/eval/questions.json` | The 50 questions with expected answers (private) |
| `tests/evals/test_*.py` | One test file per module, synthetic data only |

---

### Task 1: Question file and loader

**Files:**
- Create: `src/telltale/evals/__init__.py`, `src/telltale/evals/questions.py`, `tests/evals/__init__.py`, `tests/evals/test_questions.py`
- Create (private data): `docs/private/eval/questions.json`, `docs/private/eval/make_questions.py` (one-off helper)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) EvalQuestion(id: str, question: str, expect: Literal["reports", "no_answer"], expected_reports: tuple[str, ...], related_reports: tuple[str, ...], blind: bool)`
  - `class QuestionFileError(Exception)` and `class BlindQuestionError(Exception)`
  - `load_questions(path: Path, known_slugs: set[str]) -> list[EvalQuestion]`
  - `select(questions: list[EvalQuestion], *, final: bool, only: set[str] | None = None) -> list[EvalQuestion]`

File shape (`version` 1):

```json
{"version": 1, "questions": [
  {"id": "Q1", "question": "...", "expect": "reports", "expected_reports": ["slug-a"], "related_reports": [], "blind": false},
  {"id": "Q2", "question": "...", "expect": "no_answer", "expected_reports": [], "related_reports": ["slug-b"], "blind": true}
]}
```

- [ ] **Step 1: Write the failing tests** in `tests/evals/test_questions.py` (synthetic file in `tmp_path`):
  - `test_loads_both_kinds`: two questions load with the fields above; `expected_reports` is a tuple.
  - `test_unknown_slug_lists_every_bad_slug`: unknown slugs in two questions raise one `QuestionFileError` naming both (Review Focus 2).
  - `test_reports_question_needs_a_report`: `expect: "reports"` with empty `expected_reports` raises.
  - `test_no_answer_question_has_no_expected_reports`: `expect: "no_answer"` with non-empty `expected_reports` raises.
  - `test_duplicate_ids_rejected`, `test_unknown_version_rejected`.
  - `test_select_working_excludes_blind`: `select(qs, final=False)` returns only `blind: false`.
  - `test_select_final_returns_blind_only`: `select(qs, final=True)` returns only `blind: true`.
  - `test_only_naming_a_blind_question_without_final_refuses`: `select(qs, final=False, only={"Q2"})` raises `BlindQuestionError` whose message names `Q2` and `--final` (Review Focus 1).
  - `test_only_unknown_id_refuses`: an id not in the file raises `QuestionFileError`.
- [ ] **Step 2: Run** `uv run pytest tests/evals/test_questions.py -v`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** `questions.py` to the interfaces above. All slug problems are collected and raised once.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Build the private data.** `docs/private/eval/make_questions.py` parses the question tables in `docs/private/test-questions.md` (rows `| A<n> | ... |`): skips the row marked DROPPED; sets `blind` from the "Blind set, final" list in that file; sets `expect` to `no_answer` when the last cell starts `**No reliable answer**`; maps each report name in the last cell (bold part for `reports`, the "may be cited only as related" names for `related_reports`) to a store slug by matching against `reports.title` in `data/telltale.sqlite`. It writes `docs/private/eval/questions.json` and prints, per question, the id, the matched report titles and any name it could not match. Unmatched names are fixed by hand in the JSON, never guessed.
- [ ] **Step 6: Verify the data.** Run the helper, then load the file with `load_questions` against every slug in the store. Expected: 50 questions, 27 `reports`, 23 `no_answer`, 17 blind, 33 working, no error. The printed id-to-titles list goes to Varun with the task report, so he can check it against his picks.
- [ ] **Step 7: Commit** (private repo): `git add src/telltale/evals tests/evals docs/private/eval` then `git commit -m "feat(evals): question file loader, blind guard, private question set"`

### Task 2: Scoring saved answers

**Files:**
- Create: `src/telltale/evals/score.py`, `tests/evals/test_score.py`

**Interfaces:**
- Consumes: `EvalQuestion` (Task 1); the saved-answer dict written by `telltale.card.to_json` (keys `question`, `sentences[{text, citations[{report_title, source, cited_text}]}]`, `input_tokens`, `output_tokens`, `cost_usd`, `stop_reason`, `searched[{report_title, page, source, report_slug}]`); `is_no_answer`, `is_no_answer_result` from `telltale.answer`.
- Produces:
  - `Verdict = Literal["supported", "partly", "not_supported", "unjudged"]`
  - `answer_from_saved(saved: dict) -> telltale.models.Answer` (rebuilds `Answer`; `chunks` from `searched`), so `is_no_answer_result` is reused, not copied.
  - `@dataclass SentenceScore(index: int, text: str, cited: bool, no_answer: bool, verdict: Verdict | None)` (`verdict` None for uncited or no-answer sentences)
  - `@dataclass QuestionScore(id: str, expect: str, no_answer_result: bool, cited_slugs: list[str], retrieved_slugs: list[str], expected_cited: list[str], expected_retrieved: list[str], passed: bool, sentences: list[SentenceScore], cost_usd: float, stop_reason: str)`
  - `score_question(q: EvalQuestion, saved: dict, verdicts: dict[int, Verdict]) -> QuestionScore`
  - `@dataclass RunTotals(fact_sentences: int, uncited: int, partly: int, not_supported: int, unjudged: int, unsupported: int, unsupported_share: float, report_questions: int, report_questions_passed: int, expected_reports: int, expected_reports_retrieved: int, no_answer_questions: int, no_answer_questions_passed: int, false_no_answers: int, answer_cost_usd: float)` and `totals(scores: list[QuestionScore]) -> RunTotals`

- [ ] **Step 1: Write the failing tests** (`tests/evals/test_score.py`, dicts built in the test):
  - `test_report_question_passes_when_an_expected_report_is_cited`: a citation whose source belongs to an expected slug (via `searched`) gives `passed` True and `expected_cited == [slug]`.
  - `test_report_question_fails_on_no_answer`.
  - `test_refusal_on_report_question_is_a_miss` (`stop_reason "refusal"`; Review Focus 4).
  - `test_no_answer_question_passes_only_on_no_answer_result`; with a cited case instead, `passed` False and the cited slug listed.
  - `test_web_page_citation_maps_to_slug`: a `searched` entry with `page: null` and a GOV.UK `source`; a citation with that source maps to its slug (Review Focus 3).
  - `test_ocr_and_pdf_pages_of_one_report_map_to_one_slug`.
  - `test_uncited_sentence_is_unsupported`, `test_no_answer_sentence_is_not_a_fact_sentence`, `test_missing_verdict_counts_as_unjudged` (Review Focus 5).
  - `test_cut_off_answer_still_scores_sentences` (`stop_reason "max_tokens"`; Review Focus 4).
  - `test_totals`: three hand-built scores give the hand-worked `RunTotals`.
- [ ] **Step 2: Run** `uv run pytest tests/evals/test_score.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Rules (recorded as a Ruling in Task 7):
  - A fact sentence is any sentence that is not a no-answer sentence (`is_no_answer`).
  - Unsupported = uncited fact sentence, or verdict `partly` or `not_supported`. `partly` counts against the headline: target zero means every claim in the sentence is backed.
  - `unsupported_share` = unsupported / (fact sentences minus unjudged); unjudged is reported apart.
  - A report question passes when at least one expected report is cited. Other cited reports are listed, never penalised (expected lists are a floor). `expected_reports_retrieved` counts expected reports among the retrieved (`searched`) slugs.
  - A no-answer question passes only when `is_no_answer_result` is true. `false_no_answers` counts report questions answered with only "no reliable answer".
  - Citation to slug: match `citation.source` against `searched[].source`; failing that, `report_title` against `searched[].report_title` with the OCR note removed; failing that, slug `"?"` (counted and shown in the report).
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(evals): score saved answers"`

### Task 3: Judge

**Files:**
- Create: `src/telltale/evals/judge.py`, `tests/evals/test_judge.py`
- Modify: `src/telltale/settings.py` (add `DEFAULT_JUDGE_MODEL = "claude-sonnet-5-5"`)

**Interfaces:**
- Consumes: the saved-answer dict (Task 2 shape), `Verdict` (Task 2), `is_no_answer`.
- Produces:
  - `JUDGE_SYSTEM: str` (exact text below)
  - `build_judge_request(sentence: str, passages: list[str], model: str) -> dict`
  - `parse_verdict(response) -> tuple[Verdict, str]` (verdict and reason; anything unreadable gives `("unjudged", <why>)`)
  - `judge_answer(saved: dict, client, model: str) -> tuple[dict[int, Verdict], list[dict], float]` (verdicts by sentence index for cited fact sentences only; one record per call `{index, verdict, reason, input_tokens, output_tokens}`; judge cost in USD)

Judge system text (exact):

```text
You check one sentence from an answer against the report passages it cites.
Reply with a verdict:
- supported: every claim in the sentence is stated in the passages.
- partly: some claims are stated, at least one is not, or the sentence makes a claim stronger or more certain than the passages.
- not_supported: the passages do not state the sentence's claims.
Judge only against the passages. Do not use your own knowledge of ships, regulations or the casualty.
```

- [ ] **Step 1: Write the failing tests** with a fake client whose `messages.create` returns canned responses (the pattern in `tests/answer/test_brief.py`):
  - `test_request_holds_sentence_and_every_cited_passage`, sent as a forced tool call with input schema `{verdict: enum[supported, partly, not_supported], reason: string}`. (Changed at the final review: Claude Sonnet 5.5 refuses a forced tool call with a 400 error, so the same schema goes as structured output, `output_config.format`, with up-front thinking off.)
  - `test_parses_each_verdict`.
  - `test_unreadable_reply_is_unjudged` (no tool block; a verdict outside the enum).
  - `test_api_error_on_one_sentence_marks_it_unjudged_and_goes_on` (Review Focus 5): the fake raises `anthropic.APIError` for sentence 2; sentences 1 and 3 keep their verdicts.
  - `test_uncited_and_no_answer_sentences_are_not_sent`.
  - `test_cost_adds_up_from_usage`.
- [ ] **Step 2: Run** `uv run pytest tests/evals/test_judge.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** `max_tokens` 300; one call per cited fact sentence; the passages are the sentence's `cited_text` values, deduplicated, in order. Add `JUDGE_PRICE_INPUT_PER_MTOK` and `JUDGE_PRICE_OUTPUT_PER_MTOK` for the judge model, taken from Anthropic's current pricing page at implementation time, with the page and date in a comment (the answer prices in `answer/brief.py` are the pattern). Do not guess a price.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(evals): sentence judge"`

### Task 4: The run and the `eval` command

**Files:**
- Create: `src/telltale/evals/run.py`, `tests/evals/test_run.py`
- Modify: `src/telltale/cli.py` (new `eval` subparser and handler), `tests/test_cli.py`

**Interfaces:**
- Consumes: Tasks 1 to 3; `answer()` and `card.to_json` from stage 1; `load_index` and `_settings_with_key` in `cli.py`.
- Produces:
  - `run_eval(questions: list[EvalQuestion], ask: Callable[[str], Answer], judge: Callable[[dict], tuple[dict[int, Verdict], list[dict], float]], out_dir: Path, max_cost_usd: float, *, model: str, judge_model: str, final: bool, commit: str, dirty: bool) -> Path` (returns the run directory)
  - Run directory `data/eval/runs/<YYYYMMDD-HHMM>-<commit7>-<model>/` holding `answers/<id>.json` (the `to_json` shape), `judge/<id>.json` (the verdict records), `run.json` (`{started, commit, dirty, model, judge_model, final, question_ids, answer_cost_usd, judge_cost_usd, stopped_early, errors}`).
  - `log_final_run(run_dir: Path, log: Path = Path("data/eval/final-runs.jsonl")) -> None`
  - CLI: `telltale eval [--final] [--only A3,A10] [--model M] [--judge-model M] [--max-cost USD]` (default `--max-cost 1.00`).
- [ ] **Step 1: Write the failing tests** (fake `ask` and `judge`, `tmp_path`):
  - `test_saves_one_answer_and_one_judge_file_per_question` and a `run.json` with the fields above.
  - `test_stops_before_the_next_question_once_cost_cap_is_reached`: cap 0.01, each question costs 0.006 with its judging; two run, the third is not asked, `stopped_early` true.
  - `test_error_on_one_question_is_recorded_and_the_run_goes_on`.
  - `test_final_run_refuses_a_dirty_tree` (`dirty=True`) and `test_final_run_is_logged` with commit and date.
  - CLI: `test_eval_without_final_refuses_a_blind_id_before_any_model_call` (Review Focus 1; the fake client records no call), `test_eval_prints_the_run_directory`, `test_eval_with_no_question_file_says_where_it_should_be`.
- [ ] **Step 2: Run** `uv run pytest tests/evals/test_run.py tests/test_cli.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Questions are asked one at a time with the existing `answer()`. An exception on one question goes into `run.json` `errors[{id, error}]` (exception type and message only) and the run goes on. The question file path is `docs/private/eval/questions.json`. The CLI reads `commit` and `dirty` from `git rev-parse --short HEAD` and `git status --porcelain`.
- [ ] **Step 4: Run** the tests, then the full suite `uv run pytest -q`. Expected: all pass (250 before this stage, plus the new ones).
- [ ] **Step 5: Commit** `git commit -m "feat(evals): eval run with blind guard, cost cap and final-run log"`

### Task 5: Run report and judge review sheet

**Files:**
- Create: `src/telltale/evals/report.py`, `tests/evals/test_report.py`
- Modify: `src/telltale/cli.py` (`eval-report RUN_DIR`; `eval` calls it at the end)

**Interfaces:**
- Consumes: the run directory (Task 4), `score_question`, `totals` (Task 2), `load_questions` (Task 1).
- Produces: `write_report(run_dir: Path, questions: list[EvalQuestion]) -> Path` writing `report.md` and `judge-review.csv` in the run directory.

`report.md`, in this order: the headline "Sentences not backed by their cited passage: N of M (P%)" with uncited, partly, not supported and unjudged beside it; reports found (report questions passed; expected reports retrieved in the top 8); no reliable answer (no-answer questions passed; report questions wrongly answered "no reliable answer"); cost (answers, judge, per question); then one row per question: id, expect, passed, expected reports cited and retrieved, other reports cited, sentences, unsupported, cost. It names the run's commit, model, judge model, whether it was final, and whether it stopped early. It never prints the question text of a blind question (id only).

`judge-review.csv` columns: `id, sentence_index, sentence, cited_passages, verdict, reason, varun_verdict, varun_note`. A sample of 30 judged sentences from working questions only, drawn with a fixed seed: every `partly` and `not_supported` up to 15, the rest `supported`.

- [ ] **Step 1: Write the failing tests:** `test_headline_counts`, `test_blind_question_text_never_appears` (Review Focus 1, report side), `test_review_sheet_has_30_rows_spread_across_verdicts`, `test_review_sheet_excludes_blind_questions`, `test_report_rebuilds_from_saved_files_without_a_client`.
- [ ] **Step 2: Run** `uv run pytest tests/evals/test_report.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `write_report` and the CLI command.
- [ ] **Step 4: Run** the tests and the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(evals): run report and judge review sheet"`

### Task 6: Batch mode (the batch discount)

**Files:**
- Modify: `src/telltale/answer/brief.py` (split out `answer_from_response(question: str, chunks: list[Chunk], response) -> Answer`, used by `answer()`), `src/telltale/evals/run.py`, `src/telltale/evals/judge.py`, `src/telltale/cli.py` (`eval --batch`)
- Test: `tests/answer/test_brief.py`, `tests/evals/test_run.py`, `tests/evals/test_judge.py`

**Interfaces:**
- Produces: `answer_from_response` (above); `run_eval(..., batch: bool = False)`; batch `custom_id` is the question id for answers and `"<id>-<sentence_index>"` for judge calls.

- [ ] **Step 1: Write the failing tests:** `test_answer_and_answer_from_response_agree` (same fake response, same `Answer`); with a fake batch client, `test_batch_run_saves_the_same_files_as_a_live_run`, `test_errored_batch_item_is_recorded_and_others_kept`, `test_batch_cost_is_half_the_live_cost`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** with the SDK's Message Batches API (check the current method names in the SDK docs first; do not infer them): one batch for all answers, poll until it ends, parse; then one batch for all judge calls. Costs recorded at half the live price. The cost cap applies to the estimate before each batch is sent.
- [ ] **Step 4: Run** the tests and the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(evals): batch mode at the batch discount"`

### Task 7: First real run on the working questions, and the record

Needs the key, so it runs on Varun's Mac, and only after Varun's go-ahead to spend.

**Files:**
- Create: `docs/stage-2-results.md`
- Modify: `docs/build-log.md`, `docs/limits.md` (and its page), `docs/plans/stage-1-handover/ledger.md`, `docs/private/CONTEXT.md`

- [ ] **Step 1: Dry check.** `uv run telltale eval --only A3 --max-cost 0.05`. Expected: one answer, its judge file, and a report naming the run directory; no unjudged sentences, and `judge-review.csv` has rows.
- [ ] **Step 1b: Batch dry check.** `uv run telltale eval --batch --only A3 --max-cost 0.05`. Expected: the same files as Step 1, from an answer batch and a judge batch; no unjudged sentences, and `judge-review.csv` has rows.
- [ ] **Step 2: Working run.** `uv run telltale eval --batch --max-cost 2.00`. Expected: 33 answers, report written, `stopped_early` false. Record the actual cost.
- [ ] **Step 3: Varun's judge review.** Send Varun the run's `judge-review.csv` (private). He fills `varun_verdict`. Record agreement out of 30. Below 27 of 30, the headline is not quoted anywhere until the judge text is fixed and re-checked; record that as a Ruling.
- [ ] **Step 4: Record.** `docs/stage-2-results.md`: headline, reports found, no-answer results, cost, judge agreement, and the per-question table for working questions only (no blind id or question text). One dated build-log entry in plain words. The limits page gets the measured share of unsupported sentences. Ledger Rulings: the scoring rules (Task 2 Step 3), the judge model and text, the blind-run rule.
- [ ] **Step 5: Commit** `git commit -m "docs: stage 2 first eval run"` on the private branch. Public only via the snapshot, with Varun's go-ahead, after scanning that no blind id or question text is in it.

The blind questions are **not** run in this plan. They run once with `--final` at the end of stage 3 tuning, and then at the end of each later stage (spec: "Every later stage is measured on this set").

## Out of scope (decide later with Varun)

- Publishing the working set as the open benchmark and the model comparison table (spec section 8 addition): needs Varun's go-ahead on what is published; the blind set never is.
- Trying Sonnet 5.5 or Opus 5.5 as the answering model: one `--model` run each, once the Haiku baseline and the judge agreement are in.
- Fixing anything the run finds: stage 3 (guardrails) is where the answer changes.
