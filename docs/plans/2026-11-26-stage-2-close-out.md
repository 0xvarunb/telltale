# Stage 2 close-out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish what the spec promised for stage 2 before the 8 Dec post: a model comparison on the 33 working questions, and the working questions published as an open test set.

**Architecture:** Two more eval runs (Claude Sonnet 5.5 and Claude Opus 5.5 answering, the same Sonnet 5.5 checker), after one change so every answering model runs with its up-front thinking off, as the checker already does. A new `telltale eval-public-set` command writes the working questions and their chosen reports to a public page; Varun reviews that page before it goes anywhere.

**Tech Stack:** Python 3.12 via uv, the Anthropic Python SDK (Message Batches), pytest.

**Spec:** `docs/specs/2026-10-30-design.md` section 8 (stage 2 and its 30 Oct additions), the stage 2 plan `docs/plans/2026-11-19-stage-2-evals.md`, and the results `docs/stage-2-results.md`.

## Global Constraints

- The 17 blind questions are never run, published, named or used here. Every output is scanned for their ids and text before it leaves the private repo.
- Every eval run uses `--batch` and a `--max-cost` cap. Budget: $150 to the end of stage 6.
- A model is priced only from https://platform.claude.com/docs/en/about-claude/pricing, read on the day, with the date in a comment. A thinking setting is taken only from Anthropic's documentation, never guessed.
- A run's figures are quoted only after 30 of its checker verdicts are checked by hand and at least 27 agree (Ruling 35).
- Nothing goes to the public repo without Varun's go-ahead, through the snapshot from the separate clone.
- Never read, print or grep any `.env*` file; never print an API key.
- Plain English in every published line; no em dashes.

## Review Focus

1. An answering model with no known way to turn its thinking off: refused before any call, naming the table to add it to (Task 1 test).
2. A blind question reaching the public test set: the command refuses and writes nothing (Task 3 test).
3. A chosen report missing from the store: the command names it and writes nothing, never a blank link (Task 3 test).
4. A run whose hand-check falls below 27 of 30: its figures stay out of the comparison table (Task 2 step).
5. A run stopped by its cost cap: reported as stopped, never shown as a full 33-question result (Task 2 step; the report already says "Stopped early").

---

### Task 1: Every answering model runs with its thinking off

**Files:**
- Create: `src/telltale/thinking.py`
- Modify: `src/telltale/evals/judge.py` (use the shared table), `src/telltale/answer/prompt.py` (`build_request`), `src/telltale/pricing.py` (Opus 5.5 price), `src/telltale/cli.py` (`evaluate` refuses before any call)
- Test: `tests/test_thinking.py`, `tests/answer/test_brief.py`, `tests/test_cli.py`, `tests/test_pricing.py`

**Interfaces:**
- Produces: `THINKING_OFF: dict[str, dict]` and `class UnsupportedModel(ValueError)` and `thinking_off(model: str) -> dict` in `telltale/thinking.py`. `judge.py` keeps `UnsupportedJudgeModel` and `require_judge_model`, now built on `thinking_off`. `build_request(question, chunks, model)` adds `"thinking": thinking_off(model)`.

- [ ] **Step 1: Read the docs.** From Anthropic's documentation, read how thinking is turned off for `claude-opus-5-5` and the Opus 5.5 base price per million tokens (input, output). Write both, with the URL and date, into the task report. If the docs give no way to turn thinking off for Opus 5.5, stop and report: Opus is then left out of the comparison and the ledger records why.
- [ ] **Step 2: Write the failing tests.**
  - `test_thinking_off_per_model`: `thinking_off("claude-haiku-5-5") == {"type": "disabled"}`, `thinking_off("claude-sonnet-5-5") == {"type": "between_tools"}`, and Opus 5.5 equals the setting read in Step 1.
  - `test_thinking_off_refuses_an_unlisted_model`: `pytest.raises(UnsupportedModel, match="THINKING_OFF")`.
  - `test_answer_request_turns_thinking_off`: `build_request(q, [chunk], "claude-sonnet-5-5")["thinking"] == {"type": "between_tools"}`.
  - `test_eval_refuses_an_answer_model_with_no_thinking_setting_before_any_call` (CLI, with the `eval_offline` fixture and a priced but unlisted model): exit 1 and no `load_index` or client call.
  - `test_opus_price_is_the_price_read_on_the_pricing_page`: `PRICES["claude-opus-5-5"]` equals the Step 1 values.
- [ ] **Step 3: Run** `uv run pytest -q tests/test_thinking.py tests/answer/test_brief.py tests/test_cli.py tests/test_pricing.py`. Expected: the new tests FAIL.
- [ ] **Step 4: Implement.** Move `THINKING_OFF` from `judge.py` to `thinking.py`, add the Opus 5.5 entry from Step 1, add the Opus price with the URL and date in the comment, and refuse an unlisted answer model in `evaluate` next to `require_price`.
- [ ] **Step 5: Run** `uv run pytest -q`. Expected: all pass, including every existing judge test.
- [ ] **Step 6: Commit** `git commit -m "feat: answering models run with thinking off; Opus 5.5 priced"`

### Task 2: Two more runs, each checked by hand

No code. Run from the repository root on a clean, committed tree.

- [ ] **Step 1: Dry checks**, one question each, so a bad request costs cents:
  - `uv run telltale eval --batch --only A3 --model claude-sonnet-5-5 --max-cost 0.20`
  - `uv run telltale eval --batch --only A3 --model claude-opus-5-5 --max-cost 0.50`

  Expected: one answer and its judge file each, 0 unjudged.
- [ ] **Step 2: The runs.**
  - `uv run telltale eval --batch --model claude-sonnet-5-5 --max-cost 3.00`
  - `uv run telltale eval --batch --model claude-opus-5-5 --max-cost <three times the Opus dry check's cost x 33, rounded up to the dollar>`

  Expected: 33 of 33 answered, 0 unjudged, not stopped early. A run that stops early is reported as such and not re-run without Varun's say.
- [ ] **Step 3: Hand-check each run.** For each run's `judge-review.csv`, the controller does a first pass (verdict and a one-line note per row, in `judge-review-claude.csv`, as on 26 Nov) and Varun confirms or overrules. Record the agreement out of 30 per run. Below 27 of 30, that run's figures are not quoted and the reason is recorded as a ruling.
- [ ] **Step 4: Record** the run folders, costs and agreement in `docs/private/CONTEXT.md`.

### Task 3: `telltale eval-public-set`

**Files:**
- Create: `src/telltale/evals/public_set.py`
- Modify: `src/telltale/cli.py` (new command)
- Test: `tests/evals/test_public_set.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `EvalQuestion`, `load_questions`, `select` (`telltale.evals.questions`); `Store.reports()` for titles and page links.
- Produces: `class PublicSetError(ValueError)`; `public_set_markdown(questions: list[EvalQuestion], reports: dict[str, ReportMeta]) -> str`; CLI `telltale eval-public-set --out PATH` (default `docs/test-set.md`).

- [ ] **Step 1: Write the failing tests.**
  - `test_public_set_lists_each_working_question_with_its_chosen_reports`: two working questions in, the markdown has each id, its question text word for word, "Reports that should come up" with each report's title linked to its `page_url`, and for a no-answer question the line "The right answer: the reports don't cover this job" with any related report under "Closest case, a different job".
  - `test_public_set_refuses_a_blind_question`: a list with one `blind=True` question raises `PublicSetError` naming its id.
  - `test_public_set_refuses_a_report_not_in_the_store`: raises `PublicSetError` naming the slug.
  - `test_eval_public_set_writes_only_working_questions` (CLI, `eval_offline`): the written file has W1's text and not B1's; the command never reads the key.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.** The page opens with what the set is (33 working questions of a private 50; 17 held back and never published; how the chosen reports were picked: by a plain keyword search of every stored page and reading the passages, before Telltale was asked), then one section per question in id order. Report titles and links only; no report text.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(evals): eval-public-set writes the working questions as an open test set"`
- [ ] **Step 6: Write the page** with `uv run telltale eval-public-set`, then scan it: no blind id, none of the 17 blind question texts (check each against `docs/private/eval/questions.json`). Send it to Varun. **Varun decides** whether it is published as written, changed, or held back. Nothing goes public before his answer.

### Task 4: Record, and the public snapshot

- [ ] **Step 1:** `docs/stage-2-results.md` gains "Model comparison": one row per answering model (Haiku 5.5 from the 26 Nov re-judge, Sonnet 5.5, Opus 5.5) with: sentences not backed (n of N and %), report questions passed, no-answer questions passed, answer cost per question, and the hand-check agreement. Retrieval is the same search for every model, so "chosen reports in the 8 passages" is stated once. A model whose run failed its hand-check shows "not quoted" with the reason.
- [ ] **Step 2:** If Varun approved the test set, link `docs/test-set.md` from the results page, the README and `docs/test-questions.md`.
- [ ] **Step 3:** Build log page entry (dated, plain words), ledger ruling for the comparison (models, thinking off, what is quoted), CONTEXT and memory.
- [ ] **Step 4:** Commit privately. Public only through the snapshot, scanned, with Varun's go-ahead.
