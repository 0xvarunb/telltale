# How ATSB, NTSB and TSIB list their marine reports

Checked 26 Nov 2026 for stage 3A, Task 1. Tasks 4 to 6 (one lister per publisher) build on this.

Scope, as for MAIB: merchant vessels of 100 gross tons or over. Fishing vessels, recreational craft
and naval vessels are left out. None of the three listings gives tonnage, so the vessel type decides,
and a report is kept when at least one vessel in it is a merchant type.

How it was checked: one request at a time, at least 1.5 seconds apart, user agent
`telltale-research/0.1 (marine safety reports study)`. Report PDFs opened: two (one NTSB, one TSIB).
Pages and PDFs read are kept outside the repository. The fixtures in `tests/fixtures/sources/` are
synthetic: real field names and page structure, made-up titles, ships, numbers and dates.

## Summary

| Publisher | Listing | Marine reports | In scope | Lister can run as a script? |
| --- | --- | --- | --- | --- |
| ATSB | Not reached | Not counted | Not counted | No: the site drops every scripted request |
| NTSB | CAROL search API (`GetInvestigationsCustom`), then each investigation page | 781 investigations, 471 single-casualty reports | 356 | Only with a CAROL API key (free sign-up), or from a JSON file exported by hand |
| TSIB | One MOT page; all reports are in the page's embedded data | 72 | 72 (8 to check for tonnage) | Yes |

## Australia ATSB: blocked for scripts

### What happened

- `https://www.atsb.gov.au/` and `/robots.txt`: the TLS handshake completes (certificate for
  www.atsb.gov.au, Akamai addresses), then the server resets the request. curl over HTTP/2:
  `HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR`. Over HTTP/1.1: no reply in 60 seconds.
  Over IPv4 only: the same reset.
- gstack's headless Chromium: `net::ERR_HTTP2_PROTOCOL_ERROR` on the home page.
- After those resets, one manual visit was made in a visible desktop browser. It loaded the home
  page; `/marine-investigations` redirected to
  `/publications/2022/marine-safety-investigation-australia`, which answered HTTP 403 with the page
  "Access denied | ATSB". The next step, opening the marine listing in that browser, was refused by
  this session's own permission check as unauthorised access. Work on ATSB stopped there: no further
  ATSB page was opened by any means.
- data.gov.au has no ATSB organisation or ATSB investigation dataset (CKAN search, 26 Nov).

The 30 Oct survey saw the same thing ("listing timed out"). This is a block on automated clients,
not an outage, so a Python lister using httpx would fail the same way. The scripted requests used
the plain research user agent throughout; none was changed to pass as a browser.

### What the home page showed (structure only)

From the one page that loaded in the browser:

- Marine investigations listing:
  `https://www.atsb.gov.au/investigations?atsb_sort=occurrence_date_desc&transport_mode=609`
  (609 is marine; 607 aviation, 610 rail). Paging not seen.
- Investigation page: `https://www.atsb.gov.au/investigations/mo-2026-003`. Marine investigation
  ids look like `MO-2026-003`.
- Marine occurrence briefs are a separate listing: `/occurrence-briefs?transport_mode=609`. These are
  the short reports to keep apart from full investigations.
- Also separate: marine safety issues (`/safety-issues-and-actions?transport_mode=609`), safety
  advisory notices and REPCON confidential reports. None of these is an investigation report.

Not seen: the listing's fields, paging, counts, the report page layout, PDF links, vessel type or
tonnage fields, and the reuse notice in any report. **Task 1 Step 3 (the year ATSB reports became
CC BY 4.0) is not answered.** The 30 Oct survey's quote from the ATSB copyright statement stands, and
so does its warning that older reports carried other terms.

### What an ATSB lister could use instead

1. Ask the ATSB (email) for scripted access for a small research tool, or for a list of marine
   investigations with their report PDF links. Their answer can also settle which years are CC BY 4.0.
2. Failing that, a person collects the report details and saves each report PDF by hand in a
   browser, into `data/`, with a small file listing them. The lister then reads only those local
   files and makes no request to the ATSB site. This needs no change on the ATSB side, but every new
   report is a manual step.
3. Leave ATSB out of stage 3A until one of these works.

No ATSB fixtures were written: there is no observed structure to copy.

## US NTSB: CAROL

### Listing

The old address `https://data.ntsb.gov/carol-main-public/basic-search` now redirects to the new
CAROL at `https://my.ntsb.gov/basic`, a JavaScript app. Its search calls:

```
POST https://api.ntsb.gov/searchpub/api/Carol/v2/GetInvestigationsCustom
Header: Ocp-Apim-Subscription-Key: <key>
Body:
{"userName":"anonymous",
 "paging":{"pageSize":100,"startIndex":0},
 "sorting":{"sortingFilters":[]},
 "filter":{"operator":"All","groups":[{"operator":"All","associations":[{"operator":"All",
   "criteria":[{"field":"casedetail.TopicMode","operator":"Contains","value":"Marine"}]}]}]},
 "timeZone":"UTC"}
```

- Paging: `startIndex` and `pageSize` in the body; the response's `paging.totalCount` gives the total
  (781 on 26 Nov). The site offers page sizes of 25, 50 and 100; 100 needs 8 calls.
- Order: newest event first.
- The key: without it the API answers HTTP 401 `Access denied due to missing subscription key`. The
  CAROL web app ships its own key inside its JavaScript; a lister must not borrow that. The proper
  route is a key from the NTSB developer portal (`https://developer.ntsb.gov/signup`). Creating
  that account is Varun's call.
- The app's "Download Data (JSON)" and "Download Summary (CSV)" buttons call
  `GetCustomSearchJson` and `GetCustomSearchCsv` (v1) with the same body. A JSON file downloaded by
  hand once (Marine mode, no other filter) would let a lister run with no key, at the cost of a
  manual step for each refresh. Not downloaded here.
- The counts below come from reading the 781 results the CAROL page itself loaded in a browser.

Response shape (fixture: `ntsb_carol_investigations.json`):

```
{"paging": {"pageSize", "startIndex", "totalCount", "responseDateTime", "currentPage"},
 "results": [ {22 fields, below} ]}
```

### Fields per report

| What we need | Where it comes from | Notes |
| --- | --- | --- |
| Investigation id | `ntsbNumber` | `DCA25FM054`. Use it in the slug (`ntsb-dca25fm054`) |
| Title | `<h1>` on the investigation page | CAROL gives no title |
| Casualty date | `eventDateUtc` | `8/16/2025 5:30:00 AM`, month first, no zero padding. Empty for safety studies. The page's "Event Date" item gives `8/16/2025` |
| Published date | `publishedDate` | `09/17/2026`, or `null` while ongoing. The PDF cover carries an earlier adoption date (`August 26, 2026` for the same report) |
| Vessel names and types | `vehicles` | One string: `" Name Type,  Name Type"`, vessels separated by a comma and **two** spaces; types can contain a comma and one space (`Cargo, Liquid Bulk`). Nothing separates a name from its type: see "Reading `vehicles`" below. `vehicleDetails` is always empty |
| Tonnage | Not in CAROL. In the PDF's "Vessel Particulars" table | Example: `99 GRT (707 GT ITC)`. US register tons and ITC gross tonnage differ; a 100 GT test must use the GT ITC figure |
| Report type | `reportType` | `Report` (Marine Accident Report, `MAR`), `Board-approved brief`, `Director-approved brief` (`MIR`, older `MAB`), `Closeout memo`, `None`, or empty |
| Report number | `reportNumber` | `MIR2627`, `MAB2126`, `MAR2105`, old `MAB-10-01`; also junk such as `NA`, `Closeout`, `Memo`, `none`, `2413`, `MA1618`, `NTSBMAB1926` |
| Status | `status` | `Completed` or `Ongoing` |
| Report page URL | `https://www.ntsb.gov/investigations/Pages/{ntsbNumber}.aspx` | The CAROL app builds the same link. Plain HTML, no JavaScript needed |
| PDF URL | The `<ul class="reports-feed">` link on the investigation page | `/investigations/AccidentReports/Reports/MIR2627.pdf`, link text `MIR-26-27`. Always null in CAROL's `reportUrl`. Take the link from the page rather than building it from the number: one in-scope page (`DCA19FM033`, number `MAR`) has no link at all |
| Docket | `https://data.ntsb.gov/Docket/?NTSBNumber=...` on the page | Not needed; docket exhibits are excluded from reuse anyway |

`probableCause` and `findings` hold text from the report. Not needed for listing; the fixture's
values are invented.

Worked example: `DCA25FM054`, contact of an offshore supply vessel with a gas platform, event
16 Aug 2025, published 17 Sep 2026, vessel type `Offshore`, tonnage 99 GRT (707 GT ITC) in the PDF,
`Director-approved brief`, report page `https://www.ntsb.gov/investigations/Pages/DCA25FM054.aspx`,
PDF `https://www.ntsb.gov/investigations/AccidentReports/Reports/MIR2627.pdf` (9 pages, digital text).

### Counts (26 Nov 2026)

781 investigations with topic mode Marine (778 marine first, 2 hazmat, 1 pipeline), earliest event
date 1985 (CAROL says surface-mode data runs from 2010).

The rules, applied in this order, each to the investigations the rows above it did not take:

| Rule | Count |
| --- | --- |
| `status` is `Ongoing`: no report yet | 198 |
| `status` is `Completed` and `reportType` is `Closeout memo` (81), `None` (27) or empty (1): no report | 109 |
| `reportType` is `Report` and `reportNumber` starts with `SA` (`SA089`, `SA094`, `SA101`): a safety study, not a single casualty, keep apart. Their `ntsbNumber` has `SR` where casualties have `FM`, `MM` or similar (`DCA22SR002`), and `eventDateUtc` and `vehicles` are empty | 3 |
| **Single-casualty reports**: every other `Completed` investigation, which all have `reportType` `Report`, `Board-approved brief` or `Director-approved brief` | **471** |
| Left out: every typed vessel is `Fishing` | 94 |
| Left out: every typed vessel is `Yacht` (recreational) | 14 |
| Left out: every typed vessel is `Combatant/Military` or `Patrol/Small Craft` (naval), with no merchant vessel | 7 |
| **In scope**: at least one vessel of a merchant type | **356** |

Check: 198 + 109 + 3 + 471 = 781, and 94 + 14 + 7 + 356 = 471.

The 471 include four reports with a malformed `reportNumber` (`2413`, `MA1618`, `NTSBMAB1926`,
`NTSBMAB1929`): three in scope (two passenger, one towing), one fishing. Do not filter on the
number's pattern; take the PDF from the investigation page.

8 investigations have an empty `vehicles`. None is among the 471: 3 are the safety studies above,
and the other 5 fall in the first two rows, ongoing or completed with no report (for example
`HMD25LR001`, a closeout memo). If a later single-casualty report has no vessel, it has no merchant
vessel and is left out, so it should be counted as its own reason ("no vessel listed").

In scope by report type: `Report` 30, `Board-approved brief` 11, `Director-approved brief` 315.
In scope by vessels involved: a cargo ship or tanker in 118; towing vessels or barges only 152;
passenger only 38; offshore only 19; specialty/other only 10; other mixes 19. Collisions between a
merchant ship and a naval ship are kept (the merchant ship decides).

### Reading `vehicles`

Split the string on a comma followed by two spaces, and strip each part. Nothing separates a
vessel's name from its type, and names can hold spaces and commas, so read the type by matching
the end of each part against this list, longest first and ignoring case (`Towing/barge` occurs once):

| Type (suffix) | Group |
| --- | --- |
| `Cargo, Dry Bulk`, `Cargo, Liquid Bulk`, `Cargo, General`, then plain `Cargo` | merchant |
| `Towing/Barge`, `Offshore`, `Passenger`, `Specialty/Other` | merchant |
| `Fishing` | fishing |
| `Yacht` | recreational |
| `Combatant/Military`, `Patrol/Small Craft` | naval |

The type is the matched suffix and the name is what precedes it, minus the space.

`Specialty/Other` and free-text `Other`: check `Specialty/Other` before anything ending in `Other`.
A part that ends in ` Other` without `Specialty/` before it is not a typed vessel: CAROL uses it for
things that are not vessels (`Florida Ave. Bridge Other`, `San Pedro Bay Pipeline Other`, and once
`131 vessels and an unknown number of barges. Other`). Treat it as no vessel. A few parts carry no
type at all (a bare name such as `Pacific Glacier`); treat those as unknown too. Neither makes an
investigation in scope or out of scope on its own; only the typed vessels decide.

**Caveat on the 100 GT line:** many US towboats and small passenger boats are under 100 GT, and CAROL
cannot tell. The 152 towing-only and 38 passenger-only reports are kept by the type rule; reading the
GT ITC figure from each PDF's "Vessel Particulars" table at ingest would trim them properly.

### Fragile

- The key (above). Without one, the listing cannot run unattended.
- NTSB PDFs are encrypted (AES, opens without a password). `pypdf` needs the `cryptography` package
  to read them and fails without it: `DependencyError: cryptography>=3.1 is required for AES
  algorithm`. `src/telltale/ingest/extract.py` uses `pypdf`, and `cryptography` is not in
  `pyproject.toml`. `pypdfium2` (already a dependency) reads the same file fine. Task 5 or 7 needs one
  of these fixed before any NTSB PDF is extracted.
- `www.ntsb.gov` is SharePoint behind Cloudflare. Its marine report listing page
  (`/investigations/AccidentReports/Pages/Reports.aspx?mode=Marine`) fills its table with JavaScript
  from paths that `robots.txt` disallows (`/SiteAssets/`, `/_layouts/`), so it is not used.
  Investigation pages and `/investigations/AccidentReports/Reports/*.pdf` are allowed. No rate
  limit was met in seven requests to `www.ntsb.gov`.
- Report numbers in CAROL are hand-typed and sometimes wrong; the investigation page link is the
  reliable one.

Fixtures: `ntsb_carol_investigations.json` (one response page: in scope, multi-vessel with a
comma type, `Specialty/Other` alone, a towing vessel with a free-text `... Other` bridge, fishing,
yacht, naval, ongoing, closeout memo, safety study) and
`ntsb_investigation_page.html` (title, sidebar items, docket link, reports feed).

## Singapore TSIB: one page

### Listing

- URL: `https://www.mot.gov.sg/what-we-do/transport-investigations/marine/marine-reports/`.
  The address in the 30 Oct survey
  (`.../marine-safety-investigation-bureau/marine-safety-investigation-reports`) redirects here.
- One request gets all 72. The page shows 10 at a time with Previous/Next buttons, but paging
  happens in the browser: the server sends every item inside the page's React (Next.js) data, in
  `<script>self.__next_f.push([1,"..."])</script>` tags. Join the string parts of every push in
  order (an item list can be split across two tags), then find `"items":[{"id":"/what-we-do/...`
  and decode that JSON array. The rendered `<a>` list only holds the first 10.
- `robots.txt` allows everything except `/search`. The site is GovTech's Isomer.

### Fields per report

| What we need | Where it comes from | Notes |
| --- | --- | --- |
| Title | `title` (same as `itemTitle`) | Ends with the file size, e.g. `[PDF, 881 KB]`; strip it. A few lack it; one starts with a tab |
| Casualty date | `date` | `$D2026-01-20T00:00:00.000Z` (strip `$D`); `formattedDate` is `20 January 2026`. Matches the date in the PDF file names, so it is the casualty date, not publication |
| Published date | Not on the page. Cover of the PDF | Example: casualty 20 Jan 2026, report dated 6 Oct 2026. Some file names carry it (`..._20_sep_2023.pdf`) but not reliably |
| Vessel name and type | `description` | `Viyada Naree [Cargo Ship]`; several vessels joined by ` and `, twice with `towed by` or `towing`. Types also in the `Ship type` tag |
| Ship type | `tags[]` where `category` is `Ship type`, `selected` list | `CS: Cargo ship`, `TA: Tanker`, `TU: Tug`, `SV: Supply vessel`, `BA: Barge`, `OT: Others` |
| Tonnage | Not on the page. PDF "DETAILS OF VESSEL" table | Example: `Gross tonnage 24235` |
| Report type | `Category` tag: `Very Serious Marine Casualty` (71) or `Marine Casualty` (1); `Status` tag always `Past Reports` | All 72 are final reports; the PDF cover says "Final Report". No interim or short reports listed |
| Report page URL | `referenceLinkHref` (the PDF itself) | There is no page per report: the item `id` (`/what-we-do/.../marine-reports/<uuid>`) answers 404. So the report page and the PDF are the same link; cite and link the PDF |
| PDF URL | `referenceLinkHref` | `https://isomer-user-content.by.gov.sg/287/<uuid>/<file name>.pdf`. File names hold spaces and commas: URL-encode before fetching |
| Report number | PDF cover only | `TIB/MAI/CAS.224`; some file names carry `mib_mai_cas-068` or `tib-mai-cas-140` |

Worked example: casualty 20 Jan 2026, published 6 Oct 2026, bulk carrier (`CS: Cargo ship`),
gross tonnage 24,235, `Very Serious Marine Casualty`, final report `TIB/MAI/CAS.224`, PDF on
`isomer-user-content.by.gov.sg` (28 pages, digital text, "© 2026 Government of Singapore").

### Counts (26 Nov 2026)

72 reports, casualty dates May 2016 to January 2026. Every one involves at least one cargo ship,
tanker, supply vessel, tug, barge or other working craft; none is fishing, recreational or naval
only. **In scope: 72. Left out: 0.**

By the `Ship type` tag: cargo ship only 39, tanker only 12, tug only 4, supply vessel only 3, others
only 2, and 12 with two or three types. The "Others" tag covers a jack-up rig, harbour craft, a
dredger, a wooden coaster, a fishing vessel and a US Navy destroyer, each the other party to a
collision except the two "others only" reports (a jack-up rig, and a craft named LEO 38 whose PDF
file name also names a tanker).

To check for the 100 GT line, by reading tonnage from the PDF: the 8 reports with no cargo ship,
tanker or supply vessel (4 tug only, 2 others only, 1 tug with harbour craft, 1 tug towing a barge).

### Fragile

- The data sits in Next.js page data, not in a published API. A site rebuild can move it; the lister
  should fail loudly (ListingError naming the URL) when it cannot find the items array, not return
  an empty list.
- The published date and report number are only in the PDF.
- No rate limit or block met (page about 500 KB; one PDF fetched).

Fixture: `tsib_marine_reports_page.html` (the rendered list for one item and the full data for four,
split across two push tags as on the real page: cargo ship; tanker with an "others" craft; tug only;
"others" only).

## For Tasks 4 to 6

- ATSB (Task 4) cannot start as planned: there is no listing to build from. It needs one of the
  routes under "What an ATSB lister could use instead", and the CC BY year question stays open.
- NTSB (Task 5) needs a decision on the key: a developer portal account (Varun creates it; the key
  goes in `.env`, never in the repo), or a hand-downloaded JSON export read from `data/`. Then one
  investigation page per report for the title and the PDF link.
- TSIB (Task 6) can be built now from one page fetch.
