# Stage 3A: more reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the marine investigation reports of the Australian ATSB, the US NTSB and Singapore's TSIB to Telltale's store and search, each credited the way its publisher asks, then measure the 33 working questions again on the bigger library before any guardrail work.

**Architecture:** The store learns which publisher each report comes from (a `source` column, MAIB by default). Each new publisher gets a lister in `src/telltale/sources/` that yields `ReportMeta`, and the existing download, extract, OCR and index steps are reused unchanged. Every place that says "MAIB" or "Crown copyright" today reads the publisher's name and credit from one table instead. Done by Fri 11 Dec (roadmap).

**Tech Stack:** Python 3.12 via uv, httpx, pypdf, the existing OCR and fastembed index, SQLite, pytest.

**Spec:** `docs/specs/2026-10-30-design.md` (section 7 data, section 8 stage 1 sources), `docs/sources/2026-10-30-casualty-report-sources.md` (reuse terms per publisher), and the roadmap's stage 3 step 1.

## Global Constraints

- Reuse terms, from `docs/sources/2026-10-30-casualty-report-sources.md`:
  - ATSB: CC BY 4.0, except the Coat of Arms, the ATSB logo and third-party photos and graphics; older reports carried other terms, so each report's own notice is checked and a report with other terms is left out.
  - NTSB: US Government work, not subject to copyright; credited to the NTSB; third-party photos and docket exhibits excluded.
  - Singapore TSIB: public reports; every case taken from one names TSIB as the source and links the report itself.
- Scope, as for MAIB: merchant vessels of 100 gross tons or over. Fishing vessels, recreational craft and naval vessels are left out. Where a publisher does not give tonnage, the vessel type decides, and each left-out report is counted by reason.
- Report text and PDFs stay in `data/` (git-ignored). Test fixtures are synthetic, carry the publishers' real field names and page structure, and hold no copied report text.
- Requests are polite: one at a time, a pause between them, a descriptive generic user agent with no personal details.
- The 17 blind questions are never run by Telltale here. Their expected reports may be updated by the same keyword search used for the working ones (Task 8), since that search does not ask Telltale.
- Existing MAIB slugs, chunk ids and the 619 reports stay as they are; a re-run of `telltale ingest --source maib` gives the same store as before.
- Never read, print or grep any `.env*` file; never print an API key. Nothing goes public without Varun's go-ahead.

## Review Focus

1. A store made before this change (619 MAIB reports, no `source` column): opens, reads every old report as MAIB, and keeps its search index valid (Task 2 test).
2. An answer citing two publishers: each sentence's source shows its own publisher and credit, and the card footer names both (Task 3 test).
3. A publisher listing that changes shape or fails part way: the reports read so far are kept, the failure is named, nothing is silently dropped (Tasks 4 to 6 tests).
4. The same casualty reported by two publishers (for example a ship registered in one country that grounds in another's waters): both are kept, each under its own publisher, never merged (Task 2 test: slugs are prefixed per publisher).
5. An ATSB report whose own notice is not CC BY 4.0: left out and listed, never stored (Task 4 test).

---

### Task 1: How each publisher lists its reports (research, no product code)

**Files:**
- Create: `docs/sources/2026-11-27-atsb-ntsb-tsib-listings.md`
- Create: synthetic fixtures under `tests/fixtures/sources/` (one listing page or response and one report page per publisher)

- [ ] **Step 1:** For each of ATSB, NTSB and TSIB, record: the listing URL or export (NTSB: the CAROL export, per the sources survey), how it pages, and for one report: title, date of the casualty, date published, vessel name and type, tonnage if given, report type (final, interim, short), the report page URL and the PDF URL.
- [ ] **Step 2:** Count the marine reports and how many fall inside the scope above, by reason left out. Note any report types to keep apart (for example ATSB "short investigations").
- [ ] **Step 3:** Read the reuse notice in three ATSB reports from different years and record the year the notice became CC BY 4.0.
- [ ] **Step 4:** Write the synthetic fixtures from what was seen, keeping the real field names and HTML structure.
- [ ] **Step 5:** Commit the notes and fixtures. Send the counts to the controller, who records them in CONTEXT. Tasks 4 to 6 build on this document.

### Task 2: The store knows each report's publisher

**Files:**
- Create: `src/telltale/sources/registry.py`
- Modify: `src/telltale/models.py` (`ReportMeta.source`, `Chunk.source_id`), `src/telltale/ingest/store.py` (column, migration, reads)
- Test: `tests/sources/test_registry.py`, `tests/ingest/test_store.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) Publisher(id: str, name: str, short: str, credit: str, licence_note: str)` and `PUBLISHERS: dict[str, Publisher]` with ids `"maib"`, `"atsb"`, `"ntsb"`, `"tsib"`.
  - `publisher(source_id: str) -> Publisher`, raising `KeyError` naming the id.
  - `ReportMeta.source: str = "maib"`; `Chunk.source_id: str = "maib"` (named so it does not clash with the existing `Chunk.source` property, which is the passage's link).
  - New slugs are prefixed with the publisher id and a hyphen (`atsb-...`); MAIB slugs keep their current form.
- [ ] **Step 1: Write the failing tests.**
  - `test_publishers_give_name_and_credit`: `publisher("maib").credit == "Crown copyright"`, `publisher("atsb").credit` names CC BY 4.0 and the ATSB, `publisher("ntsb").credit` credits the NTSB, `publisher("tsib").credit` names the Transport Safety Investigation Bureau, Singapore.
  - `test_old_store_reads_as_maib`: a store file built with the current schema and two reports opens, `reports()` gives `source == "maib"` for both, and `chunks()` keeps their ids.
  - `test_reports_of_two_publishers_with_the_same_title_are_both_kept`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.** Add `source TEXT NOT NULL DEFAULT 'maib'` to `reports`, with an `ALTER TABLE` for an old store as the `ocr` column already does; carry it through `upsert_report`, `reports()` and `chunks()`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(ingest): the store records each report's publisher"`

### Task 3: Every credit and label comes from the publisher

**Files:**
- Modify: `src/telltale/answer/prompt.py` (the "This passage is from the MAIB report" line), `src/telltale/cli.py` (`CROWN_COPYRIGHT` in the sources list), `src/telltale/card.py` (`CROWN_COPYRIGHT` footer), `src/telltale/web/templates/index.html` (per-source credit), `src/telltale/ingest/store.py` (`labelled_title` import)
- Test: `tests/answer/test_brief.py`, `tests/test_cli.py`, `tests/test_card.py`, `tests/web/test_app.py`

**Interfaces:**
- Consumes: `publisher()` from Task 2.
- Produces: `labelled_title(title: str, report_type: str, source_id: str) -> str`, moved to `registry.py`; MAIB titles are unchanged.

- [ ] **Step 1: Write the failing tests.**
  - `test_request_names_each_passage_publisher`: a chunk with `source_id="ntsb"` gives the line `This passage is from the NTSB report: <title>.`; a MAIB chunk keeps today's line word for word.
  - `test_cli_sources_show_each_publisher_credit`: an answer citing a MAIB and an ATSB report prints "Crown copyright" after the first and the ATSB credit after the second.
  - `test_card_footer_names_every_publisher_cited`: the footer reads `Reports: UK MAIB, Crown copyright; ATSB, CC BY 4.0` (exact text set in Task 2's table) when both are cited, and today's text when only MAIB is.
  - `test_page_shows_each_source_credit`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** with the table from Task 2; no publisher name is written anywhere else.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass, and every existing MAIB-only test unchanged.
- [ ] **Step 5: Commit** `git commit -m "feat: each source shows its own publisher and credit"`

### Tasks 4, 5 and 6: One lister per publisher (ATSB, then NTSB, then TSIB)

Each is its own task, with its own reviewer, in this order. Same shape for all three.

**Task 4 (ATSB) not done: left out of 3A by Varun on 27 Nov 2026 ("Leave ATSB out of 3A").** The ATSB website refuses scripted requests; it is not worked around. The `atsb` entry stays in the publisher table and `--source atsb` stays refused with that reason.

**Files (per publisher `X`):**
- Create: `src/telltale/sources/X.py`
- Test: `tests/sources/test_X.py`, using the Task 1 fixtures

**Interfaces:**
- Consumes: Task 1 document and fixtures; `ReportMeta`, `SkippedReport` and `ListingError` (reuse the MAIB ones, moved to `telltale/sources/common.py` in Task 4).
- Produces: `list_reports(client: httpx.Client, *, limit: int | None = None, on_skip: Callable[[SkippedReport], None] | None = None) -> list[ReportMeta]`, every `ReportMeta` with `source="X"` and a prefixed slug.

- [ ] **Step 1: Write the failing tests:**
  - `test_lists_in_scope_reports_with_their_pdf`: from the fixture, the expected titles, dates, vessel types, report page URLs and PDF URLs.
  - `test_out_of_scope_reports_are_skipped_with_a_reason` (fishing, recreational, naval).
  - `test_limit_stops_after_n`.
  - `test_a_failed_page_raises_listing_error_naming_the_url`.
  - ATSB only: `test_report_without_cc_by_notice_is_skipped`.
  - TSIB only: `test_every_report_links_its_own_report_page`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** from the Task 1 document.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(sources): list X marine reports"`

### Task 7: `telltale ingest --source`

**Files:**
- Modify: `src/telltale/cli.py` (`ingest`, parser), README (how to run)
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `telltale ingest [--source maib|atsb|ntsb|tsib|all] [--limit N]`, default `maib`, so today's command does what it does today. The index is always rebuilt from the whole store.

- [ ] **Step 1: Write the failing tests:** `test_ingest_source_lists_only_that_publisher`, `test_ingest_all_lists_each_publisher_in_turn`, `test_ingest_default_is_maib`, `test_ingest_prints_counts_per_publisher`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement**; the end-of-run counts are printed per publisher.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(cli): ingest by publisher"`

### Task 8: The real ingest, and the expected reports brought up to date

No new code. Run on a clean, committed tree.

- [ ] **Step 1:** `uv run telltale ingest --source atsb --limit 5`, then `--source ntsb --limit 5`, then `--source tsib --limit 5`. Check each stored report against its page by hand: title, dates, vessel, PDF.
- [ ] **Step 2:** The full runs, one publisher at a time, output saved under `data/results/`. Record per publisher: reports stored, left out by reason, pages read by OCR, not searchable.
- [ ] **Step 3:** For every one of the 50 questions, search the new reports with the same plain keyword search used on 19 Nov (not Telltale's own search) and read the matching passages. Apply the picking rule: same or close job counts, same hazard but a different job is "related" only. A "no reliable answer" question that now has a same-job report becomes a report question. Record each change in `docs/private/test-questions.md` with the reason, rebuild `docs/private/eval/questions.json` with `make_questions.py`, and send the list of changes to Varun.
- [ ] **Step 4:** Commit the private files.

### Task 9: The new baseline

- [ ] **Step 1:** `uv run telltale eval --batch --max-cost 1.50` on the 33 working questions with the bigger library (Haiku 5.5, thinking off).
- [ ] **Step 2:** Hand-check 30 verdicts (controller's first pass, Varun confirms); at least 27 of 30 before quoting.
- [ ] **Step 3:** Ask Telltale the crankcase question a reader sent on 19 Nov again, and record whether the new reports answer it.
- [ ] **Step 4:** Record in a new `docs/stage-3-results.md`, section "Starting point": the library by publisher and the baseline beside the 26 Nov MAIB-only figures, so the effect of the new reports is shown apart from any guardrail work.

### Task 10: Stage-end housekeeping for this part

- [ ] **Step 1:** Limits page and its in-app copy: which publishers are covered, how many reports each, what is left out and why.
- [ ] **Step 2:** README data credit per publisher; build log page entry; ledger rulings (publishers added, scope rule, credits, slug prefixes); CONTEXT and memory.
- [ ] **Step 3:** Commit privately. Public only through the snapshot, scanned, with Varun's go-ahead.
