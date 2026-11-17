# Stage 1 handover (for a cloud or later session)

## Status after the cloud session (30 Oct 2026)

**Done.** Tasks 1 to 8 are built, each reviewed by its own reviewer, and the whole branch had a final review (verdict: ready for the local real runs). 172 tests pass, also with the network cut off. An offline end-to-end run with fakes (synthetic PDFs, mocked GOV.UK, the real Anthropic SDK over a mock transport, the web page through a test client) passed, including an ingest killed half way and resumed with no duplicates or re-downloads. Branch `stage-1-report-search` is pushed; nothing was merged into `main`. Ledger: `ledger.md` (every task, ruling and deferred minor).

What exists: `telltale ingest [--limit N]`, `telltale ask "..."`, `telltale serve` (page at http://127.0.0.1:8000 with a `/limits` page), the README with the skills map, `docs/concepts.md`, `docs/build-log.md`, `docs/limits.md`, and `docs/stage-1-results.md` (every figure marked "Not yet measured", with the command that produces it).

**Real runs still owed** (this cloud container had no `ANTHROPIC_API_KEY`, and its network blocked www.gov.uk, assets.publishing.service.gov.uk and huggingface.co; Ruling 9). Run them locally from the repo root, in this order:
1. Task 6 Step 5: `uv run telltale ingest --limit 30`, then `uv run telltale ask "We are about to enter a ballast tank on a bulk carrier. What has gone wrong on other ships?"`. Expect at least one report cited and a cost under $0.02. This is also the first real load of the embedding model (then cached in `data/models`).
2. Task 6 Step 6: `uv run telltale ingest` (full, about 635 listing calls plus downloads). Record the counts and the four printed lists (no PDF link, PDF but no text, errors, left out of the listing).
3. Task 7 Step 5: `uv run telltale serve`, ask the ballast-tank question in the browser, take the screenshot for the 24 Nov post.
4. Task 8 Step 3 measured parts: fill every "Not yet measured" in `docs/stage-1-results.md`, then the matching figures in `docs/limits.md` and `src/telltale/web/templates/limits.html` (keep both in step; a test compares them) and the stage 1 entry in `docs/build-log.md`. Varun chooses the expected report for each of the three questions.
While doing these, check whether the prompt's "never stand in for the permit to work..." clause makes the model add uncited caveat sentences (Ruling 20); shorten it if so.

**Before the repo goes public:** Varun looks at `.env.example` (no agent may read `.env*` files) and the personal email in `pyproject.toml` authors. The public repository is made from a snapshot of this one (one first commit, no history).

**Rulings made in the cloud session** (full text in `ledger.md`):
- Ruling 9: no key and blocked hosts here, so build and unit-test everything and leave the real runs owed.
- Ruling 10: the controller pushes `stage-1-report-search` after each task; subagents never push.
- Ruling 11: ingest lists every report that has a PDF link but no text (`Store.without_text()`); per-report errors never abort the run.
- Ruling 12: `telltale ingest` always rebuilds and saves the search index.
- Ruling 13: the system prompt adds "stop there: do not offer the closest case or stretch an unrelated case to fit the job".
- Ruling 14: the sentence splitter breaks after a single capital initial when a common sentence opener follows.
- Ruling 15: a listing item that cannot be read becomes a "dropped" report, listed with its page link, instead of stopping the listing (`on_skip` callback).
- Ruling 16: `ingest` does not need the API key; `ask` and `serve` do.
- Ruling 17: the embedding model is cached in `data/models`.
- Ruling 18: the refused and cut-off notices are shared by the command line and the page; `Citation.page` added.
- Ruling 19: the page refuses a question posted from another website (Origin differs from Host).
- Ruling 20: the system prompt adds "Never say or imply that a job is safe or safe to proceed, and never stand in for the permit to work, the risk assessment, the SMS or the office's permission."
- Ruling 21: the limits page is served at `/limits` and mirrors `docs/limits.md`; skills map rows for stages 2 to 6 keep the spec's wording.
- Ruling 22: the question checks (empty, no word character, over 500 characters) are shared by `ask` and the page.

## Original handover notes

Read in this order:
1. `ledger.md` — what is done (all of Tasks 1 to 8 complete and reviewed, see the status above), every ruling, and deferred minors. Resume at the first task without a `Task <N>: complete` line.
2. `constraints.md` — the rules every task and review must follow.
3. `../2026-10-30-stage-1-report-search.md` — the plan. 4. `../../specs/2026-10-30-design.md` — the spec.

How to continue: use superpowers:subagent-driven-development on the plan, branch `stage-1-report-search`.
Start your own SDD workspace, then copy `ledger.md` into it as `progress.md` so completed tasks are not re-run.
Every implementer and reviewer: model opus; never read or print `.env*`; never push to main or merge.
Ruling 7 in the ledger: fix Task 2's retry, redirect and silent-drop gaps inside Task 6 before the full ingest.
Task 6 real runs need ANTHROPIC_API_KEY in the environment and internet access to www.gov.uk and assets.publishing.service.gov.uk.
After each task, append the ledger lines to `docs/plans/stage-1-handover/ledger.md` too and commit it, so the next session can resume.
