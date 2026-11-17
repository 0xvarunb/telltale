# Stage 1 results

**The ingest figures below were measured on 6 Nov 2026 (full ingest on Varun's Mac). The question
figures are still owed.** Stage 1 was built and unit-tested in a cloud session on 30 Oct
2026 that had no Anthropic API key and no access to the report sites (ledger, Ruling 9), so the
real runs happen locally.

Every figure not yet measured is marked "Not yet measured" with the exact command that will produce
it. Only figures written here may be used in the
17 Nov and 24 Nov posts. Rule 0 applies to this page too: no estimate goes in a "Measured" cell.

- Date measured: ingest 6 Nov 2026; questions not yet measured
- Commit measured on: ingest at f575763 (branch stage-1-report-search); questions not yet measured

## Before you start

Run everything from the repository root, with `ANTHROPIC_API_KEY` in `.env` (see the README).
Outputs are saved under `data/results/`, which is git-ignored: they contain report text and must
not be committed.

```
mkdir -p data/results
```

Small run first (plan, Task 6 Step 5), to check the whole chain before the long one:

```
uv run telltale ingest --limit 30 2>&1 | tee data/results/ingest-30.txt
uv run telltale ask "We are about to enter a ballast tank on a bulk carrier. What has gone wrong on other ships?" | tee data/results/ask-30.txt
```

Expected: an answer citing at least one report, and a cost under $0.02. Then the full run (Task 6
Step 6), which makes one request per report and takes a while:

```
uv run telltale ingest 2>&1 | tee data/results/ingest-full.txt
```

It must print "Search index saved to data/index" just after the counts (the report lists follow
it). If instead it says "The search index was not built", the figures below are not valid, so
re-run `uv run telltale ingest`.

## The reports

All from the end of the ingest output of 6 Nov 2026 (commit f575763: full report picked over the
synopsis, the four report types of Ruling 23, OCR of scanned pages from Ruling 24, GOV.UK page
text for reports with no PDF from Ruling 26).

| Figure | Measured | Where it is printed |
| --- | --- | --- |
| Reports listed | 619 | "Listed N reports." |
| Reports stored, by type | 619: investigation report 397, completed preliminary examination 180, safety bulletin 32, investigation report on behalf of the Red Ensign Group 10 | "Reports:" under "Store data/telltale.sqlite, all runs so far" |
| Reports with a PDF link | 439 | "Reports with a PDF link:" |
| Reports read from their GOV.UK page (no PDF) | 180 | "Reports read from their GOV.UK page (no PDF):" |
| Page groups indexed (the unit the search returns) | 23,519 | "Page groups (chunks):" |
| Passages indexed | 150,590 | "Passages:" |
| Pages read by OCR (scanned pages with no text layer) | 1,248 | "Pages read by OCR:" |
| Reports with no PDF link and no page text, not searchable | 0 | the count after "Reports with no PDF link and no page text, not searchable" |
| Reports with a PDF link but no text | 0 | the count after "Reports with a PDF link but no text (download failed, unreadable or image-only PDF), not searchable" |
| Errors during the run | 0 | the count after "Errors this run" |
| Reports left out of the listing because they could not be read | 0 | the count after "Left out of the listing because they could not be read" |
| Other MAIB report types left out | 16 (overseas report 13, discontinued investigation 3) | "Other report types left out:" |

So all 619 stored reports have searchable text: 439 from their PDFs and 180 (the completed
preliminary examinations, which MAIB publishes with no PDF) from their GOV.UK pages. The 619 and
16 together make up the 635 GOV.UK entries tagged "merchant vessel 100 gross tons or over".

### Earlier runs (superseded)

Do not quote these figures.

- 5 Nov 2026, commit d7e056d: the same 619 reports, 439 with a PDF link, 23,234 page groups,
  148,173 passages, 1,248 pages read by OCR, 0 errors. The 180 preliminary examinations were not
  searchable, because page text was not yet read.
- 4 Nov 2026, commit 1cde374, investigation reports only: 397 reports, 397 with a PDF link, 33
  with no text, 12,263 page groups, 80,902 passages, 0 errors, 238 other types left out. That run
  picked the one-page synopsis instead of the full report for 163 older reports, and could not
  read scanned pages.

## Departments (5 Nov 2026)

From `uv run telltale departments` on the 619 stored reports, sorted by words in their titles
(Ruling 25; a first pass, still to be checked by Varun in `data/departments.csv`):

| Department | Reports |
| --- | --- |
| Bridge and navigation | 347 |
| Deck | 182 |
| Engine | 80 |
| Catering and accommodation | 6 |
| In more than one department | 70 |
| Not sorted (title too general) | 77 |

## Three real questions

Proposed questions (Varun may change them). The expected report for each is to be chosen by
Varun from the ingested list before the question is asked, so the check is fair.

| # | Question | Expected report | Expected report in the top 8? | Sentences | Unsourced sentences | Cost |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | We are about to enter a ballast tank on a bulk carrier. What has gone wrong on other ships? | Fall while entering ballast tank on container vessel Ville de Mars with loss of 1 life (proposed after the first attempt; Varun to confirm) | Yes, places 1, 2 and 8 (pages 7, 11, 14) | 12 | 3 | $0.0016 (6,860 in, 1,829 out) |
| 2 | We are about to moor alongside on a container ship. What has gone wrong on other ships during mooring? | Parting of mooring rope from container vessel Fremantle Express with loss of 1 life (proposed; Varun to confirm) | Yes, place 8 (page 7) | 29 | 8 | $0.0037 (7,856 in, 5,761 out) |
| 3 | We are about to lower the lifeboat for a drill on a ro-ro passenger ferry. What has gone wrong on other ships? | Failure of lifeboat suspension link on ro-ro passenger ferry Pride of Hampshire with 16 people injured (proposed; Varun to confirm) | Yes, place 2 (page 5) | 30 | 13 | $0.0024 (7,021 in, 3,400 out) |

**First attempt at question 1, 6 Nov 2026 (commit 60c4a38, prompt before Ruling 27).** Not a
result: the prompt was changed because of it. The top 8 held 3 page groups of the Ville de Mars
report (a fall while entering a ballast tank on a container vessel, places 1, 2 and 8) and 5 from
unrelated reports. The model turned Ville de Mars down because the question named a bulk carrier,
and answered with 2 unsourced sentences saying the reports do not cover the job. Cost $0.0010
(6,740 tokens in, 681 out).

**Second attempt at question 1, 6 Nov 2026 (commit bf7c631, after Ruling 27).** Not a result
either: the prompt changed again (Ruling 28). The answer used Ville de Mars and said it was a
container ship. 14 sentences, 7 cited (all to page 7) and 7 unsourced. Of the 7 unsourced, 3 were
about the results ("the only report here", "the extracts do not say", "none of the other reports
here"), 1 was a summary, and 3 were facts left uncited; each of those 3 is stated on page 7
(checked against the PDF), one with its certainty softened ("almost certain" became "most
likely"). Pages 11 and 14, also in the top 8, hold only figure captions. Cost $0.0017 (6,804 in,
2,013 out).

**Question 1, measured 6 Nov 2026 (commit e4bb9e8, prompt of Ruling 28).** The row above is this
run. Its 12 sentences were checked one by one against page 7 of the Ville de Mars PDF:

- 9 cited sentences: all cite page 7, and 7 of them state what page 7 says. Sentences 11 and 12
  miscount the recommendations: page 7 gives two (one on complacency, which also aims to share the
  methods through the MCA's Human Element Advisory Group, and "a further recommendation" on the
  audit regime), but the answer makes the sharing aim "another" recommendation and calls the audit
  one "a third". This is the limit already stated on the limits page: a cited sentence is not yet
  checked against its passage (stage 3).
- 3 unsourced sentences: sentence 1 (true, stated on page 7, but uncited), sentence 6 (a link
  between paragraphs, "The report also points to earlier warning signs") and sentence 9 (calls
  CMA CGM Group "the ship's operator", which page 7 does not say).
- No sentence about the search results; the report's "almost certain" kept.

**Question 2, measured 6 Nov 2026 (commit 2aef027, same prompt as question 1).** Top 8: Cherry
Sand page 7, Alfa Britannia page 23 (OCR), Hurlingham pages 30 and 27, Zarga report page 15, Rema
page 58, Zarga safety bulletin page 5, Fremantle Express page 7; 6 of 8 about mooring. Checked
against the PDFs of Fremantle Express, Cherry Sand, Hurlingham and the Zarga bulletin:

- No invented fact found. Sentence 11 calls UK Dredging "the operator", which page 7 does not say
  (the same small drift as "the ship's operator" in question 1).
- Form: 4 pairs of an uncited plain sentence followed by a cited near-quote (1-2, 3-4, 16-17,
  24-25); 3 cited sentences that only repeat a report title (2, 17, 25; they cite the title line
  Telltale adds to each result); report wording pasted in ("This section will examine...").
- Sentence 27 cites figure labels as a sentence: Zarga bulletin page 5 holds only figure captions.
  Caption-only pages also reached the top 8 in question 1 (Ville de Mars pages 11 and 14).
- Fremantle Express page 7 gives the lessons (degraded rope not retired, no AB at the forward
  station, a toolbox meeting); the answer used only its title and one recommendation.

**Question 3, measured 6 Nov 2026 (commit 365b6d0, same prompt as questions 1 and 2).** Top 8:
Norsea pages 16 and 23 (OCR), Pride of Hampshire page 5, Herald of Free Enterprise page 46,
Hebrides page 18, P&OSL Aquitaine (chute drill) page 46, P&OSL Aquitaine (bowsing gear, OCR) page 7,
European Highway page 25; 6 of 8 about lifeboats or evacuation drills. Checked against the Norsea
and European Highway PDFs:

- Sentence 21 (unsourced) is wrong: it says the hydrostatic interlock "stops the boat being
  lowered when it is not clear"; the report defines it as "a system intended to prevent the hooks
  opening unless the boat is afloat". The definition came from the model, not the report; the
  "no source" mark flagged it.
- Sentence 2 cites the line Telltale adds to each search result ("This passage is from the MAIB
  report: ..."), not report text.
- The other unsourced Norsea sentences (3, 5, 6, 8) are supported by page 16. 9 of the 13
  unsourced sentences are the uncited half of a paraphrase and cited-quote pair.

For each question N (1, 2, 3), with its text in place of `QUESTION`:

1. **The top 8.** This prints the 8 page groups the search finds, which are exactly the ones sent
   to the model. It needs no API key. Mark "yes" if the expected report is among them, with its
   place.

   ```
   uv run python -c "import sys; from pathlib import Path; from telltale.cli import load_index; [print(f'{n}. {c.report_title}, page {c.page}') for n, c in enumerate(load_index(Path('data')).search(sys.argv[1], k=8), start=1)]" "QUESTION" | tee data/results/top8-N.txt
   ```

   A traceback ending in "No report store at data/telltale.sqlite" means `telltale ingest` has
   not run yet (or this is not the repository root). One ending in "The search index is missing
   or out of date" means the last ingest did not build the index; re-run it.

2. **The answer.**

   ```
   uv run telltale ask "QUESTION" | tee data/results/answer-N.txt
   ```

3. **The counts.**

   ```
   grep -cE '^[0-9]+\. ' data/results/answer-N.txt   # sentences (numbered lines)
   grep -c '\[no source\]' data/results/answer-N.txt  # unsourced sentences
   grep '^Cost:' data/results/answer-N.txt            # cost of this answer
   ```

   A plain "There is no reliable answer in these reports for that job." line is not numbered and
   not counted: it claims no fact.

## Summary figures

| Figure | Measured | How |
| --- | --- | --- |
| Expected report in the top 8 | 3 of 3 (expected reports proposed, Varun to confirm) | out of 3, from the table above |
| Share of unsourced sentences | 24 of 71 (34%) | total unsourced sentences divided by total sentences, over the three answers |
| Cost per answer | $0.0016, $0.0037, $0.0024; average $0.0026 | each answer's "Cost:" line, and their average |

These are the first baseline, measured 6 Nov 2026 on one prompt (commit e4bb9e8 onwards, Ruling
28). A prompt revision for the patterns found (Ruling 29) is to be measured on the same three
questions; its figures will be added beside these, not in place of them.

### After Ruling 29 (commit 966e2b0, 6 Nov 2026)

Same questions, new prompt. These questions were used to shape the prompt, so these figures
flatter it; the stage 2 eval set with its held-out part is the fair measure.

| # | Run | Sentences | Unsourced | Cost |
| --- | --- | --- | --- | --- |
| 1 | A | 12 | 3 | $0.0016 (6,964 in, 1,891 out) |
| 2 | A | 25 | 6 | $0.0032 (7,960 in, 4,900 out) |
| 2 | B (same question asked again) | 19 | 2 | $0.0029 (7,960 in, 4,149 out) |
| 3 | A | 19 | 4 | $0.0019 (7,125 in, 2,343 out) |
| 3 | B (on the page, for the 24 Nov screenshot, 17 Nov) | 25 | 3 | $0.0027 (7,125 in, 4,053 out) |
| 3 | C (on the page with the logo, the 24 Nov screenshot, 17 Nov) | 28 | 4 | $0.0030 (7,125 in, 4,586 out) |

- Over the first run of each question: 13 of 56 sentences unsourced (23%), against 24 of 71
  (34%) in the baseline; average cost $0.0022 ($0.0016, $0.0032, $0.0019), against $0.0026.
- Two runs of question 2 on the same prompt gave 6 of 25 and 2 of 19 unsourced: one run per
  question is noisy.
- Question 1 checked against page 7: all 9 cited sentences correct, and the recommendations are
  now counted as the page gives them (one with two aims, and a further one). Unsourced: two true
  sentences without their citation, and one calling CMA CGM Group "the owner" (page 7 names it
  only).
- Gone in all three runs: paraphrase-then-quote pairs, figure captions cited as sentences, and
  equipment explained from the model's own knowledge.
- Still seen: run 2A has two sentences that only repeat the "This passage is from the MAIB
  report:" line (2 and 24) and the pasted "This section will examine..."; most remaining unsourced
  sentences open a case from its title ("On the X, ..."); question 3 sentence 4 calls Schat-Davit
  BV "the equipment's manufacturer" (page 16 says "Schat-Davit BV representatives"); question 3
  sentence 17 and run 2B sentence 9 quote a sentence that needs the one before it ("Neither took
  place", "before he took action").
- Question 3 run B (the page, 17 Nov), checked against the passages shown in its sources panel:
  22 cited sentences match their passages; one cited sentence again calls the Schat-Davit BV
  representatives "the equipment makers' staff", which page 16 does not say; sentence 6 ("After
  one of them used two hands.") is a fragment of "He then used two hands". Unsourced: the Pride of
  Hampshire and European Highway openers (both true) and one linking sentence ("The report points
  to quality control.").
- Question 3 run C (the page, 17 Nov; the screenshot for the 24 Nov post), checked against the
  passages in its sources panel: 24 cited sentences match their passages. This time the
  unsupported "manufacturer's staff" claim came as its own sentence ("Crew members took over the
  operation of the equipment from the manufacturer's staff.") and was marked "no source". The
  other unsourced sentences are three short lead-ins ("The failure came from a broken fitting.",
  "The inspection also found other faults.", "The report points to quality control."). Sentence 25
  adds "Because the work had been repeated without incident", which page 25 says (section 2.5)
  but the cited passage does not; sentence 28 calls the report's "should be introduced" a
  recommendation.

## Other checks owed (no figures)

- **The page, for the 24 Nov post** (plan, Task 7 Step 5): `uv run telltale serve`, open
  http://127.0.0.1:8000, ask question 1 and take a screenshot. Varun approves it before use.
- **A job the reports do not cover** (Review Focus 1): ask
  `uv run telltale ask "We are about to replace a crankcase relief valve. What has gone wrong on other ships?"`
  and check the answer says plainly that there is no reliable answer in these reports, without
  offering the closest case.
