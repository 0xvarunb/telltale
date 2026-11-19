# Stage 1: Report Search (RAG) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An officer types the job their ship is about to do and gets matching cases from UK MAIB merchant-ship investigation reports, each sentence linked to the report passage it came from, on a local web page and a command line.

**Architecture:** An ingest pipeline lists MAIB merchant-ship investigation reports through the GOV.UK APIs, downloads each PDF once, splits the text into small citable passages grouped by page, and stores them in SQLite. A hybrid search (keyword BM25 plus a small local embedding model, fused by reciprocal rank) finds the 8 best page groups. Claude Haiku 5.5 answers from those groups only, using the API's `search_result` blocks with citations on, and every sentence of the answer carries the passages it cites; a sentence with none is marked "no source" (stage 1 marks, stage 3 removes).

**Tech Stack:** Python 3.12 (via `uv`), httpx, pypdf, fastembed (`BAAI/bge-small-en-v1.5`, CPU, no torch), numpy, rank-bm25, anthropic SDK, FastAPI + Jinja2, pytest.

**Scope:** UK MAIB merchant-ship investigation reports only. ATSB follows in its own short plan; NTSB once its reuse terms are confirmed. The tool always says which reports it searched.

**Spec:** `docs/specs/2026-10-30-design.md` (approved 30 Oct 2026). Evidence for the citation approach: `spikes/2026-10-30-cited-answer/RESULTS.md`.

## Global Constraints

- Rule 0: the tool never invents a fact. Answers use only the retrieved passages; no procedures, figures or regulation text from the model's own knowledge.
- Lead each case with what happened and how it ended, then how, then what the report says would have prevented it. No blame. No checklist, form or anything to sign. Never "safe to proceed".
- Every case links to its report (PDF and page) and carries "Crown copyright" credit. Text only; no report photos or figures.
- Model: `claude-haiku-5-5`. Prices for cost display: $0.10 per million input tokens, $0.50 per million output tokens. Do not send `temperature`, `top_p` or `top_k`.
- Embedding model runs locally on CPU (offline target is a CPU-only ship's office laptop).
- Public repo rules: never commit `.env`, API keys, report PDFs, extracted report text, the SQLite store or vectors (`data/` and `spikes/**/data`, `spikes/**/.venv`, `spikes/**/result-*.json` are git-ignored). Test fixtures are synthetic and name no real ship, person or company.
- Never push to GitHub without Varun's go-ahead for that push.
- UI copy: British spelling, no em dashes, plain words. Fonts EB Garamond (headings), Geist (text), JetBrains Mono (figures); colours paper `#f4f1ea`, ink `#13283b`, orange `#e8501a`, green `#2f6b4a`.
- Commits end with: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## Review Focus

1. **A job the reports don't cover** (for example "replace a crankcase relief valve"): the page must say plainly there is no reliable answer in these reports, not stretch an unrelated case into one. Pinned in Task 5 (prompt) and Task 7 (page renders it).
2. **Numbers and abbreviations inside sentences** ("15.87m", "No. 3(P)", "e.g.", "approx."): must not split a sentence, or citations attach to the wrong half (the spike hit "15." splitting). Pinned in Task 5.
3. **Reports with no downloadable PDF** (8 of the spike's first 21 picks): recorded with their page link, listed after ingest, never silently missing. Pinned in Tasks 2 and 3.
4. **Odd questions on the page**: empty, whitespace, or over 500 characters must give a friendly message, never a server error. Pinned in Task 7.
5. **Ingest stopped halfway** (network drop, laptop sleep): re-running continues without duplicates or re-downloads. Pinned in Task 3.

---

### Task 0 (Varun): API key

- [ ] Create a project-only key in the Anthropic console with a $150 spend limit, and put it in `.env` at the repo root as `ANTHROPIC_API_KEY=...`. Never print it.

### Task 1: Repository and settings

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `src/telltale/__init__.py`, `src/telltale/settings.py`, `src/telltale/models.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `load_settings(env_file: Path | None = None) -> Settings` with fields `anthropic_api_key: str`, `data_dir: Path` (default `data/`), `model: str` (default `"claude-haiku-5-5"`). Missing key raises `SettingsError("ANTHROPIC_API_KEY is not set. Add it to .env")`.
- Produces in `models.py` (dataclasses, used by every later task):
  - `ReportMeta(slug: str, title: str, page_url: str, published: str, occurred: str | None, vessel_types: list[str], report_type: str, pdf_url: str | None)`
  - `Chunk(id: int, report_slug: str, report_title: str, pdf_url: str, page: int, passages: list[str])` with property `source -> str` returning `f"{pdf_url}#page={page}"`
  - `Citation(report_title: str, source: str, cited_text: str)`
  - `Sentence(text: str, citations: list[Citation])` with property `sourced -> bool`
  - `Answer(question: str, sentences: list[Sentence], chunks: list[Chunk], input_tokens: int, output_tokens: int, cost_usd: float, stop_reason: str)`

- [ ] **Step 1:** `git init` at the project root, branch `main`; `uv init --package --name telltale --python 3.12` then add dependencies: `uv add httpx pypdf fastembed numpy rank-bm25 anthropic fastapi uvicorn jinja2 python-multipart` and `uv add --dev pytest respx`. `.gitignore` covers the public-repo rules above plus `.venv/`, `__pycache__/`. Package and command name `telltale` (the tool is named Telltale, chosen by Varun 30 Oct 2026).
- [ ] **Step 2: Failing tests** `test_missing_key_raises` (empty env file, `SettingsError` with the exact message above) and `test_reads_key_and_defaults` (env file with `ANTHROPIC_API_KEY=test-key` gives `model == "claude-haiku-5-5"`, `data_dir == Path("data")`).
- [ ] **Step 3:** Run `uv run pytest tests/test_settings.py -v`. Expected: FAIL (module missing).
- [ ] **Step 4:** Implement `settings.py` (parse `KEY=value` lines; environment variable wins over file) and `models.py`.
- [ ] **Step 5:** Run again. Expected: PASS.
- [ ] **Step 6:** Commit the existing `docs/` (design, outreach, plans, linkedin drafts and card) and the spike's code and RESULTS.md together with the scaffold. Check first with `git status --ignored` that no PDF, `.env`, `chunks.json` or `result-*.json` is staged. Message: `chore: scaffold briefing package and settings`.

### Task 2: MAIB source

**Files:**
- Create: `src/telltale/sources/__init__.py`, `src/telltale/sources/maib.py`
- Test: `tests/sources/test_maib.py`, fixtures `tests/fixtures/govuk_search_page.json`, `tests/fixtures/govuk_content_attachments.json`, `tests/fixtures/govuk_content_body_link.json`, `tests/fixtures/govuk_content_no_pdf.json`

**Interfaces:**
- Consumes: `ReportMeta`.
- Produces: `list_merchant_reports(client: httpx.Client, page_size: int = 100, limit: int | None = None) -> list[ReportMeta]` and `pick_pdf_url(content: dict) -> str | None`.

Wire values (checked live 30 Oct 2026; fixtures must carry these exact shapes and keys):
- Search: `GET https://www.gov.uk/api/search.json?filter_format=maib_report&filter_vessel_type=merchant-vessel-100-gross-tons-or-over&fields=title,link,public_timestamp&count=<n>&start=<offset>`; response `{"total": int, "results": [{"title", "link", "public_timestamp"}]}`. On 30 Oct `total` was 635.
- Content: `GET https://www.gov.uk/api/content<link>`; `details.metadata` holds `vessel_type` (list), `report_type` (for example `"investigation-report"`), `date_of_occurrence`; `details.attachments[]` holds `url`, `content_type`, `file_size`.

- [ ] **Step 1: Failing tests:**
  - `test_pages_through_results`: respx mocks two search pages (`start=0`, `start=2`, `count=2`, `total=3`); asserts 3 content calls and the right slugs in order.
  - `test_keeps_investigation_reports_only`: a content fixture with `report_type: "safety-bulletin"` is dropped.
  - `test_pick_pdf_prefers_largest_attachment`: two PDF attachments, returns the larger one's `url`.
  - `test_pick_pdf_falls_back_to_body_link`: no attachments, body contains `https://assets.publishing.service.gov.uk/media/abc/report.pdf`; returns it.
  - `test_pick_pdf_none_when_missing`: returns `None`, and `list_merchant_reports` still returns that report with `pdf_url is None`.
- [ ] **Step 2:** Run `uv run pytest tests/sources -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Pause 0.2 s between content calls; send a `User-Agent` naming the project.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: list MAIB merchant-ship investigation reports`.

### Task 3: Ingest and store

**Files:**
- Create: `src/telltale/text.py`, `src/telltale/ingest/__init__.py`, `src/telltale/ingest/download.py`, `src/telltale/ingest/extract.py`, `src/telltale/ingest/store.py`
- Test: `tests/test_text.py`, `tests/ingest/test_extract.py`, `tests/ingest/test_store.py`, `tests/ingest/test_download.py`

**Interfaces:**
- Consumes: `ReportMeta`, `Chunk`.
- Produces:
  - `split_sentences(text: str) -> list[str]` in `text.py`: does not split after a number followed by a digit, after `No.`, `e.g.`, `i.e.`, `approx.`, `Fig.`, `para.`, or single capital initials. Task 5 uses it too.
  - `download_pdf(client: httpx.Client, meta: ReportMeta, dest: Path) -> Path | None` (skips when the file exists and is non-empty; `None` when `meta.pdf_url` is `None`).
  - `pdf_pages(path: Path) -> list[str]`, `to_passages(page_texts: list[str], max_chars: int = 220) -> list[tuple[int, str]]` (1-based page, passage) and `group_by_page(passages: list[tuple[int, str]], size: int = 12) -> list[tuple[int, list[str]]]`.
  - `Store(path: Path)` with `upsert_report(meta: ReportMeta) -> None`, `replace_chunks(slug: str, groups: list[tuple[int, list[str]]]) -> None`, `chunks() -> list[Chunk]`, `missing_pdf() -> list[ReportMeta]`, `counts() -> dict[str, int]` (keys `reports`, `with_pdf`, `chunks`, `passages`).

- [ ] **Step 1: Failing tests:**
  - `test_split_keeps_decimals_and_abbreviations`: "The vessel was 15.87m long and No. 3(P) tank flooded, e.g. through a fracture. Nobody was hurt." gives exactly 2 sentences.
  - `test_joins_hyphenated_line_breaks`: `["The fire-\nfighting team"]` gives one passage containing `"firefighting team"`.
  - `test_passage_breaks_after_max_chars_on_sentence_end`: three 100-char sentences give two passages; no passage splits mid-sentence.
  - `test_keeps_page_numbers`: text on pages 1 and 3 keeps pages 1 and 3.
  - `test_upsert_twice_keeps_one_row` and `test_replace_chunks_replaces`: re-ingesting the same report leaves `counts()["reports"] == 1` and the newer chunks only.
  - `test_missing_pdf_listed`: a report stored with `pdf_url=None` appears in `missing_pdf()`.
  - `test_download_skips_existing_file`: an existing non-empty file makes no HTTP call (respx asserts zero calls).
- [ ] **Step 2:** Run `uv run pytest tests/test_text.py tests/ingest -v`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: download, extract and store MAIB report passages`.

### Task 4: Hybrid search

**Files:**
- Create: `src/telltale/search/__init__.py`, `src/telltale/search/embed.py`, `src/telltale/search/index.py`
- Test: `tests/search/test_index.py`

**Interfaces:**
- Consumes: `Chunk`.
- Produces:
  - `class Embedder(Protocol): def embed(self, texts: list[str]) -> np.ndarray` (rows L2-normalised, float32). `FastEmbedder(model: str = "BAAI/bge-small-en-v1.5")` implements it.
  - `rrf(rankings: list[list[int]], k: int = 60) -> list[int]`.
  - `HybridIndex.build(chunks: list[Chunk], embedder: Embedder) -> HybridIndex`, `.save(dir: Path) -> None`, `HybridIndex.load(dir: Path, chunks: list[Chunk], embedder: Embedder) -> HybridIndex`, `.search(query: str, k: int = 8) -> list[Chunk]`. Each ranker returns its top 40 before fusion. The index text for a chunk is its report title plus its passages.

- [ ] **Step 1: Failing tests** (with a deterministic fake embedder that hashes words into a 16-dim vector):
  - `test_rrf_rewards_agreement`: `rrf([[1,2,3],[3,1,2]])` puts 1 first.
  - `test_keyword_match_ranks_first`: among three synthetic chunks, the one containing "ballast tank" ranks first for "entering a ballast tank".
  - `test_save_load_same_results`: results identical after save and load.
  - `test_returns_at_most_k`: `k=2` returns 2.
- [ ] **Step 2:** Run `uv run pytest tests/search -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Vectors saved as `vectors.npy` plus `chunk_ids.json`; BM25 is rebuilt from chunk text at load time.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: hybrid keyword and embedding search over report passages`.

### Task 5: Cited answer

**Files:**
- Create: `src/telltale/answer/__init__.py`, `src/telltale/answer/brief.py`, `src/telltale/answer/prompt.py`
- Test: `tests/answer/test_brief.py`

**Interfaces:**
- Consumes: `Chunk`, `Citation`, `Sentence`, `Answer`, `split_sentences`, `HybridIndex.search`.
- Produces:
  - `SYSTEM_PROMPT: str` in `prompt.py` (the spike's final wording; see `spikes/2026-10-30-cited-answer/src/run.py`), which must contain "Use only the search results provided", "no reliable answer in these reports", "No blame", and "name the ship inside the first sentence about it".
  - `build_request(question: str, chunks: list[Chunk], model: str) -> dict` returning `model`, `max_tokens=8000`, `system`, and one user message whose content is one `search_result` block per chunk (`source=chunk.source`, `title=f"{report_title} (page {page})"`, `citations={"enabled": True}`, content = a first text block `f"This passage is from the MAIB report: {report_title}."` then one text block per passage), followed by the question as a text block.
  - `parse_sentences(blocks: list) -> list[Sentence]`: joins the response's text blocks, splits with `split_sentences`, and gives each sentence the union of citations of every block it overlaps (the spike's fixed algorithm). Drops fragments under 4 words and markdown `*`/`#` debris.
  - `answer(question: str, index: HybridIndex, client: anthropic.Anthropic, model: str) -> Answer`, computing `cost_usd` from `usage` at the prices in Global Constraints.

- [ ] **Step 1: Failing tests** (fake response objects shaped like the SDK's: `.content` blocks with `.type`, `.text`, `.citations` items with `.title`, `.source`, `.cited_text`; `.usage.input_tokens`, `.usage.output_tokens`; `.stop_reason`):
  - `test_request_shape`: model is `claude-haiku-5-5`; no `temperature`, `top_p` or `top_k` keys; every `search_result` has citations enabled and starts with the title block.
  - `test_sentence_spanning_two_blocks_gets_both_citations`.
  - `test_uncited_sentence_marked_unsourced`: `sourced is False`.
  - `test_decimal_sentence_keeps_its_citation`: a cited block "The vessel was 15.87m long." stays one sentence with its citation.
  - `test_cost`: 20,000 input and 1,300 output tokens cost `0.00265`.
  - `test_prompt_keeps_rules`: `SYSTEM_PROMPT` contains the four phrases above.
- [ ] **Step 2:** Run `uv run pytest tests/answer -v`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5:** Commit: `feat: cited answers from retrieved report passages`.

### Task 6: Command line and the real ingest

**Files:**
- Create: `src/telltale/cli.py`; entry point `telltale = "telltale.cli:main"` in `pyproject.toml`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `telltale ingest [--limit N]` (list, download, extract, store, build the index, then print `counts()` and every report in `missing_pdf()` with its page link), `telltale ask "<question>"` (prints numbered sentences, `[no source]` on unsourced ones, a numbered source list, and the cost), `telltale serve` (Task 7).

- [ ] **Step 1: Failing test** `test_ask_prints_no_source_marker` with a stub `answer` function returning one sourced and one unsourced sentence; asserts `[no source]` appears once and the source list has one entry.
- [ ] **Step 2:** Run `uv run pytest tests/test_cli.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement with `argparse`.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5: Real run, small:** `uv run telltale ingest --limit 30`, then `uv run telltale ask "We are about to enter a ballast tank on a bulk carrier. What has gone wrong on other ships?"`. Expected: an answer citing at least one report, and a cost printed under $0.02.
- [ ] **Step 6: Real run, full:** `uv run telltale ingest` (run in the background; about 635 content calls plus the downloads). Record the printed counts and the missing-PDF list.
- [ ] **Step 7:** Commit: `feat: command line for ingest and ask`.

### Task 7: The web page

**Files:**
- Create: `src/telltale/web/__init__.py`, `src/telltale/web/app.py`, `src/telltale/web/templates/index.html`, `src/telltale/web/static/style.css`
- Test: `tests/web/test_app.py`

**Interfaces:**
- Consumes: `Answer`.
- Produces: `create_app(answer_fn: Callable[[str], Answer]) -> FastAPI` with `GET /` (the form) and `POST /ask` (form field `question`, answer rendered on the same page).

Page content (exact copy):
- Heading: "What went wrong on other ships doing this job?"
- Field hint: "Describe the job, the ship type and anything else that matters."
- Each sentence ends with numbered source marks; an unsourced sentence shows "no source" in orange.
- Sources panel: report title, page, the cited passage, a link to the PDF page, and "Crown copyright".
- Empty question: "Type the job you are about to do." Over 500 characters: "Please keep it under 500 characters."
- Footer: "Stage 1 of 6: RAG (retrieval-augmented generation). The tool first finds the report passages that match your job, then writes only from them. Reports: UK MAIB, Crown copyright."

- [ ] **Step 1: Failing tests** with a stub `answer_fn`:
  - `test_form_renders`: `GET /` is 200 and contains the heading.
  - `test_answer_shows_sources_and_no_source_marker`.
  - `test_empty_question_friendly`: `POST /ask` with `"   "` is 200 with the empty-question message, and the stub is not called.
  - `test_long_question_friendly`: 501 characters gives the length message.
  - `test_no_reliable_answer_renders`: a stub answer whose only sentence says there is no reliable answer in these reports renders it, with no sources panel.
- [ ] **Step 2:** Run `uv run pytest tests/web -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Same design language as `docs/linkedin/assets/post-01-card.html`. No JavaScript framework.
- [ ] **Step 4:** Run again. Expected: PASS.
- [ ] **Step 5: Real check:** `uv run telltale serve`, ask the ballast-tank question in the browser, and take a screenshot for the 24 Nov post.
- [ ] **Step 6:** Commit: `feat: local web page for cited briefings`.

### Task 8: README, skills map and stage 1 results

**Files:**
- Create: `README.md`, `docs/concepts.md`, `docs/stage-1-results.md`, `docs/build-log.md`, `docs/limits.md`
- Modify: `src/telltale/web/templates/index.html` (footer link to the limits page)

**Interfaces:**
- Consumes: the real outputs of Tasks 6 and 7.

- [ ] **Step 1:** `README.md`: what the tool is (two sentences), rule 0, how to run (`uv sync`, `.env`, `telltale ingest`, `telltale serve`), data credit (MAIB, Open Government Licence v3.0, Crown copyright), and a **Skills map** table with one row per stage: Stage | AI concept | In plain words | Where in the code | Post. Only stage 1's row is filled ("RAG: find the matching report passages, then answer only from them", `src/telltale/search`, `src/telltale/answer`, Tue 24 Nov); stages 2 to 6 are listed as "coming".
- [ ] **Step 2:** `docs/concepts.md`: a plain-English entry for each concept used so far (RAG, embeddings, keyword search, citations), each tied to a file in the repo. No concept is described that the code does not use.
- [ ] **Step 3:** `docs/stage-1-results.md`: measured figures only, from the Task 6 and 7 runs: reports listed, reports with PDFs, passages indexed, missing PDFs, three real questions with whether the expected report appeared in the top 8, the share of unsourced sentences, and the cost per answer. These are the facts the 17 Nov and 24 Nov posts may use.
- [ ] **Step 4:** `docs/build-log.md`, newest entry first, each entry: date, stage, what worked, what failed, measured numbers, cost. Two entries: 30 Oct 2026 (the test on 21 MAIB reports, figures from `spikes/2026-10-30-cited-answer/RESULTS.md`, including that the model alone left 21 of 60 sentences unsourced) and the stage 1 finish (figures from `docs/stage-1-results.md`).
- [ ] **Step 5:** `docs/limits.md` ("What this tool can't do"): it covers only the MAIB merchant-ship investigation reports listed in the results (and names the reports with no PDF); it does not know about any accident outside them; in stage 1 it marks unsupported sentences but does not yet remove them, and the measured share is stated; it never says a job is safe, and never replaces the permit to work, the risk assessment, the SMS or the office's permission. Add a footer link "What this tool can't do" on the web page, and a test in `tests/web/test_app.py` (`test_footer_links_limits_page`) asserting the link is present.
- [ ] **Step 6:** Run the full suite: `uv run pytest -v`. Expected: all pass.
- [ ] **Step 7:** Commit: `docs: README, skills map, build log, limits page and stage 1 results`. Do not push.
