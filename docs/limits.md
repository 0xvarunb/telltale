# What this tool can't do

Telltale, stage 3. This page is updated at every stage. The same text is shown on the tool's own
page at `/limits` (`src/telltale/web/templates/limits.html`); change both together.

## It covers MAIB, NTSB and TSIB merchant-ship reports only

The page names only the publishers whose reports are in the store, and shows the lines for those
publishers only. This copy is written for all three, as stored in the full runs of 6 Nov 2026
(MAIB) and 27 Nov 2026 (NTSB and TSIB). With MAIB reports alone in the store, the heading reads
"It covers MAIB merchant-ship reports only".

- It searches only reports from the UK Marine Accident Investigation Branch (MAIB) published on
  GOV.UK for merchant vessels of 100 gross tons or over: full investigation reports, investigations
  done on behalf of the Red Ensign Group, safety bulletins and preliminary examinations. Each case
  says which kind it is; a preliminary examination is not a full investigation. Overseas reports and
  discontinued investigations are left out.
- It searches reports from the US National Transportation Safety Board (NTSB) listed in a saved
  export of CAROL, NTSB's search: Marine Accident Reports and briefs, each on a single casualty.
  Safety studies, closeout memos and investigations still open are left out. CAROL gives no
  tonnage, so the vessel type decides: a report is kept when at least one of its vessels is a
  cargo ship, tanker, passenger vessel, towing vessel or barge, offshore vessel or other working
  vessel. Reports on fishing vessels, yachts or naval vessels alone are left out. Each report's
  page is its NTSB investigation page.
- It searches the final reports of the Transport Safety Investigation Bureau, Singapore (TSIB)
  listed as past reports on the Ministry of Transport's marine safety investigation reports page.
  The page gives no tonnage, so the ship type decides: a report is kept when at least one of its
  ships is a cargo ship, tanker, supply vessel, tug, barge or other working craft. Reports on
  fishing, recreational or naval vessels alone are left out. TSIB publishes no web page for each
  report, so each case links to the report's PDF itself. The listing gives the date of the
  casualty, not the date the report was published.
- Some older reports are scanned pages. Their text is read by OCR (software that reads text from a
  picture of a page), which can misread words or figures. Passages read this way are marked
  "scanned page, text read by OCR"; check them against the linked PDF page.
- MAIB publishes a preliminary examination as the text of its GOV.UK page, with no PDF. The tool
  reads that page text (not its pictures) and links to the page, which has no page numbers.
- It has no reports from any other accident investigator yet. Reports from the Australian
  Transport Safety Bureau (ATSB) are not available: the ATSB website refuses scripted requests.
- With MAIB reports alone in the store, the page says instead: It has no reports from any other
  country's accident investigators yet.
- It knows only the reports in its store as of the last `telltale ingest`. A report published
  after that is not included until `telltale ingest` is run again.
- How many reports it covers: `telltale ingest` prints the current figures for each publisher.
- In the full run of 6 Nov 2026 it stored 619 MAIB reports, all with searchable text: 439 from
  their PDFs and 180 preliminary examinations from their GOV.UK pages.
- In the full run of 27 Nov 2026 it stored 353 NTSB reports.
- In the full run of 27 Nov 2026 it stored all 72 TSIB reports on the listing, each with a PDF
  link: 71 on a very serious marine casualty and 1 on a marine casualty.

## Reports it cannot search

Some reports have neither a PDF the tool can download nor text on their web page, some PDFs have
no readable text (for example scanned pages), and some report pages cannot be read at all. None
of these can be searched. `telltale ingest` prints each one with its page link (or "no page link"
when the publisher's listing gave it none), under these headings:

- "Reports with no PDF link and no page text, not searchable"
- "Reports with a PDF link but no text (download failed, unreadable or image-only PDF), not
  searchable"
- "Left out of the listing because they could not be read"

MAIB, in the full run of 6 Nov 2026: none. 0 reports had neither a PDF link nor page text, 0 had
a PDF link but no text, and 0 were left out of the listing.

NTSB, in the full run of 27 Nov 2026: 3 were left out of the listing because they could not be
read. Each one's investigation page links no report PDF: DCA19FM033 (the collision between the
Genesis River and the Voyager tow), DCA13LM021 (the Herbert C. Jackson's allision with a bridge)
and DCA13NM002 (its page read "Page not found").

NTSB investigations out of scope are left out too: 425 in that run. 198 are still open, with no
report yet. 109 have no report: 81 have a closeout memo instead, 27 list no report and 1 has no
report type. 115 are on fishing, recreational or naval vessels only. 3 are safety studies, not a
single casualty.

Two NTSB investigation pages list more than one PDF. For each, the accident report was chosen by
reading the covers on 27 Nov 2026: for the Dali (DCA24MM031), report MIR-25-40, not the bridge
safety document MIR-25-10; for El Faro (DCA16MM001), report MAR-17/01, not the safety
recommendation report or the digest. Any other page with more than one PDF is left out and named.

TSIB, in the full run of 27 Nov 2026: none. 0 reports had neither a PDF link nor page text, 0 had
a PDF link but no text, and 0 were left out of the listing.

It reads only the text of each report. Photographs and drawings are not used, so anything shown
only in a picture is unknown to it.

## It knows nothing outside those reports

- Answers come only from the report passages it finds for your question. The model is told to
  add nothing from its own knowledge: no procedures, regulations, figures or advice the passages
  do not state.
- It does not know about any accident that is not in those reports. It does not know your
  company's own incidents, fleet circulars, your SMS, the maker's manual or the regulations.
- For each question it reads only the 8 best-matching groups of passages, each from one report
  page. A relevant case outside those 8 can be missed. On the stage 3 test questions (27 Nov
  2026), 24 of the 112 reports chosen in advance as the right ones were among those 8. On the 69
  chosen when the store held MAIB reports alone, 16 were among those 8, down from 25 on 26 Nov:
  the bigger library pushed relevant reports out of the 8 (`docs/stage-3-results.md`).
- When nothing fits, it says there is no reliable answer in these reports. That means only that
  the reports it found do not cover the job. It does not mean the job has no hazards.
- It does not always say so when it should. On the stage 3 test questions (27 Nov 2026), for 11
  jobs the reports don't cover, it said there was no reliable answer on 4. On the other 7 it
  offered cases from a different job. Check that a case it gives is the same job as yours.

## So far in stage 3 it marks unsupported sentences but does not remove them

- Every sentence of an answer should come from a report passage. A sentence the model wrote
  without citing a passage is shown with "no source" on the page (`[no source]` on the command
  line). The tool marks these sentences; it does not yet remove them. Removing them is planned
  for stage 3 and is not built yet.
- A sentence with a source links to the passage the model cited, but the tool does not yet
  check, when it answers, that the passage really supports the sentence. Read the passage before
  you rely on it.
- The measured share of unsourced sentences in stage 1: 13 of 56 sentences (23%) over three real
  questions with the current prompt, and 24 of 71 sentences (34%) with the first prompt, on 6 Nov
  2026 (`docs/stage-1-results.md`). One of the earlier ones explained a piece of lifeboat
  equipment wrongly, from the model's own knowledge. The prompt was adjusted using these same
  questions, so on new questions the share may be higher.
- Measured on 33 test questions at the start of stage 3 (27 Nov 2026, with reports from three
  publishers): 133 of 617 sentences (21.6%) were not fully backed. 88 cited nothing, and of the
  cited ones a checker model judged 45 only partly backed by their passage and 0 not backed at
  all. The checker's verdicts were checked by hand: 28 of 30 agreed (`docs/stage-3-results.md`).
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

Reports: UK MAIB, Crown copyright, used under the Open Government Licence v3.0; US NTSB, US
Government work, not subject to copyright in the US; Singapore TSIB, public reports, each cited
and linked.

The page credits only the publishers whose reports are in the store. With MAIB reports alone, its
credit line reads: Reports: UK MAIB, Crown copyright, used under the Open Government Licence v3.0.
