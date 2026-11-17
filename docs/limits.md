# What this tool can't do

Telltale, stage 1. This page is updated at every stage. The same text is shown on the tool's own
page at `/limits` (`src/telltale/web/templates/limits.html`); change both together.

## It covers MAIB merchant-ship reports only

- It searches only reports from the UK Marine Accident Investigation Branch (MAIB) published on
  GOV.UK for merchant vessels of 100 gross tons or over: full investigation reports, investigations
  done on behalf of the Red Ensign Group, safety bulletins and preliminary examinations. Each case
  says which kind it is; a preliminary examination is not a full investigation. Overseas reports and
  discontinued investigations are left out.
- Some older reports are scanned pages. Their text is read by OCR (software that reads text from a
  picture of a page), which can misread words or figures. Passages read this way are marked
  "scanned page, text read by OCR"; check them against the linked PDF page.
- MAIB publishes a preliminary examination as the text of its GOV.UK page, with no PDF. The tool
  reads that page text (not its pictures) and links to the page, which has no page numbers.
- It has no reports from any other country's accident investigators yet.
- It knows only the reports in its store as of the last `telltale ingest`. A report published
  after that is not included until `telltale ingest` is run again.
- How many reports it covers: in the full run of 6 Nov 2026 it stored 619 MAIB reports, all with
  searchable text: 439 from their PDFs and 180 preliminary examinations from their GOV.UK pages.
  `telltale ingest` prints the current figures.

## Reports it cannot search

Some MAIB reports have neither a PDF the tool can download nor text on their GOV.UK page, some PDFs have no readable text (for
example scanned pages), and some report pages cannot be read at all. None of these can be
searched. `telltale ingest` prints each one with its GOV.UK page link (or "no page link" when
the GOV.UK search gave it none), under these headings:

- "Reports with no PDF link and no page text, not searchable"
- "Reports with a PDF link but no text (download failed, unreadable or image-only PDF), not
  searchable"
- "Left out of the listing because they could not be read"

In the full run of 6 Nov 2026 none: 0 reports had neither a PDF link nor page text, 0 had a PDF
link but no text, and 0 were left out of the listing.

It reads only the text of each report. Photographs and drawings are not used, so anything shown
only in a picture is unknown to it.

## It knows nothing outside those reports

- Answers come only from the report passages it finds for your question. The model is told to
  add nothing from its own knowledge: no procedures, regulations, figures or advice the passages
  do not state.
- It does not know about any accident that is not in those reports. It does not know your
  company's own incidents, fleet circulars, your SMS, the maker's manual or the regulations.
- For each question it reads only the 8 best-matching groups of passages, each from one report
  page. A relevant case outside those 8 can be missed.
- When nothing fits, it says there is no reliable answer in these reports. That means only that
  the reports it found do not cover the job. It does not mean the job has no hazards.

## In stage 1 it marks unsupported sentences but does not remove them

- Every sentence of an answer should come from a report passage. A sentence the model wrote
  without citing a passage is shown with "no source" on the page (`[no source]` on the command
  line). Stage 1 marks these sentences; it does not yet remove them. Removing them is planned for
  stage 3.
- A sentence with a source links to the passage the model cited, but stage 1 does not yet check
  that the passage really supports the sentence. Read the passage before you rely on it.
- The measured share of unsourced sentences in stage 1: 13 of 56 sentences (23%) over three real
  questions with the current prompt, and 24 of 71 sentences (34%) with the first prompt, on 6 Nov
  2026 (`docs/stage-1-results.md`). One of the earlier ones explained a piece of lifeboat
  equipment wrongly, from the model's own knowledge. The prompt was adjusted using these same
  questions, so on new questions the share may be higher.
- For comparison only: in the 30 Oct 2026 test on 21 MAIB reports, before this tool was built,
  21 of 60 fact sentences came back without a supporting passage.

## It never says a job is safe

It tells you what went wrong on other ships. It never says a job is safe, or "safe to proceed".
An answer with no matching cases does not mean the job is safe.

## It never replaces your own process

It never replaces the permit to work, the risk assessment, the SMS or the office's permission.
It brings other ships' experience to the toolbox meeting; the decisions stay with the ship and
the office.

## It needs the internet and an API key to answer

- The search runs on the laptop, but each answer is written by a model reached over the
  internet, and each question costs a small amount of API credit.
- The page has no login. Anyone who can reach it can ask questions on your API credit, so keep
  it on this computer (127.0.0.1, the default).

Reports: UK MAIB, Crown copyright, used under the Open Government Licence v3.0.
