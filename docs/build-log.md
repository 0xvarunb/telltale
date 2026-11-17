# Build log

One dated entry per stage, newest first: what worked, what failed, measured numbers, what it
cost. Only measured figures go here; anything not yet measured says so.

## 30 Oct 2026: stage 1 built and tested (measured figures owed)

**Stage:** 1, RAG (find the matching report passages, then answer only from them).

**What was built.**

- Listing of MAIB merchant-ship investigation reports through the GOV.UK APIs, with retries, and
  every report that cannot be read listed with its page link: `src/telltale/sources/maib.py`,
  `src/telltale/http.py`.
- Download, text extraction, short passages grouped by page, and a SQLite store. Re-running
  carries on without duplicates or downloading again: `src/telltale/ingest/`.
- Hybrid search: keyword search (BM25) and a small embedding model run on the CPU, merged by
  rank fusion: `src/telltale/search/`.
- Cited answers from Claude Haiku 5.5, given the passages as search results with citations on.
  Every sentence carries the passages it cites, or is marked "no source": `src/telltale/answer/`.
- The commands `telltale ingest`, `telltale ask` and `telltale serve` (`src/telltale/cli.py`),
  and the local page with its "What this tool can't do" page (`src/telltale/web/`).

**What was tested.** 172 tests pass (`uv run pytest -q`). They make no network calls and need no
API key. Tasks 1 to 7 each passed a separate code review.

**What failed, or needed fixing.** All found in code review and all fixed before the real runs:

- Bold markers in the model's reply hid the end of a sentence, so a sentence with no source could
  borrow its neighbour's citation. Found and fixed in the cited-answer task (Task 5).
- The test for "no reliable answer" matched any unsourced sentence containing those words, which
  would have hidden an unsourced claim behind it. Found and fixed in the command-line task
  (Task 6).
- The first listing code had no retries, so one failed request out of hundreds would have stopped
  the whole run. Found in the review of the listing task (Task 2), and fixed later, in the
  command-line task before its full ingest (Task 6, Ruling 7).

Not done: the real runs. The cloud session had no API key, and its network refused www.gov.uk,
assets.publishing.service.gov.uk and huggingface.co. So no report was downloaded, no search index
was built over real reports, and no question was put to the model. The local session runs them
next (`docs/stage-1-results.md` lists the exact commands).

**Measured numbers.** Not yet measured: reports listed, reports with a PDF, passages indexed,
missing PDFs, whether the expected report is in the top 8 for three real questions, and the share
of unsourced sentences. All are owed in `docs/stage-1-results.md`.

**Cost.** Cost per answer: not yet measured. The cloud session had no API key, so it made no paid
model calls.

## 30 Oct 2026: the citation test on 21 MAIB reports (before stage 1)

**Stage:** before stage 1. A throwaway test, not the product
(`spikes/2026-10-30-cited-answer/`).

**The question.** Can every sentence of a cited answer be backed by a report passage, and does an
automatic check catch the sentences that are not?

**Set-up.** 21 MAIB reports (Open Government Licence), 1,628 passages. Keyword search (BM25), top
8 chunks sent to Claude Haiku 5.5 as search results with citations on. A second Haiku 5.5 call
checked every sentence against the passages it cites. Planted test: 3 cited sentences altered on
purpose (a changed number or tank number, or an invented ship type and port).

**Measured numbers.**

| Run | Question | Fact sentences | Without support | Planted caught | Cost |
| --- | --- | --- | --- | --- | --- |
| 1 | ballast tank entry, bulk carrier | 29 | 14 | 3 of 3 | $0.0071 |
| 2 | same, sentence splitting fixed, stricter prompt | 23 | 8 | 3 of 3 | $0.0073 |
| 3 | same, report titles citable | 24 | 7 | - | $0.0060 |
| 4 | crankcase relief valve (not covered by the 21 reports) | 13 | 6 | - | $0.0033 |

Run 1's counts are inflated by a sentence-splitting bug in the test, fixed for run 2.

**What failed.**

- The model alone does not meet rule 0. In runs 2 to 4, 21 of 60 fact sentences (about a third)
  came back without a supporting passage, mostly the model's own summary lines.
- One sentence added outside knowledge: it named the IMO where the passage said only "the revised
  Resolution".
- On a job the reports did not cover, the model said there was no reliable answer in these
  reports, then offered the closest case.
- Keyword search alone missed some obvious enclosed-space cases.
- Deleting the unsupported sentences left awkward text (a dangling "It", lost ship names).

**What worked.**

- The sentence check caught every unsupported sentence we looked at, and 6 of 6 planted errors,
  including small ones (50 cm changed to 80 cm, tank No 3 changed to No 6). A spot check of five
  sentences it passed: all genuinely matched their cited passage.
- The check is strict: a true detail from elsewhere in the report still fails if it is not in the
  cited passage. That is right for "cite or stay silent".

**Cost.** Under 1 US cent per question, including the checks.

**What it changed.** The promise "every sentence is backed by a line in a report, or it is
dropped" is achievable with the check, and not by prompting the model alone. So stage 1 marks
every sentence without a source, adds semantic search beside the keywords, and tells the model to
stop at "no reliable answer" rather than offer the closest case. The check that removes
unsupported sentences, and a rewrite of what is left, are stage 3.
