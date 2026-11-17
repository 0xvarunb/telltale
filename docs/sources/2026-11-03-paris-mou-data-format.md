# Paris MoU inspection data: format notes

Read 3 Nov 2026 from the two public samples on parismou.org/datasharing-public/. Both are Excel workbooks holding the same 49 inspections; neither is a raw XML file, whatever the button labels say (the two download links are also swapped). The samples are never committed: they hold real ship, company and charterer details.

- "XML import example in Excel DS v2.xlsx" (inside the ZIP behind "Example XML file (ZIP)") adds a "Table suggestion" sheet.
- "XML import example in Excel DS.xlsx" (behind "XML import example in Excel DS") carries the XML schema itself, as an Excel XML map, plus a Notes sheet of screenshots showing how to load the XML into Excel. The schema below is taken from that map.

## What the sample is

- One sheet, "XML file", flattens the XML into one row per one-to-many item: 1,005 rows for 49 inspections, first visits from 23 Jul to 11 Nov 2020.
- A second sheet, "Table suggestion", gives the Paris MoU's own advice: split the data into tables, because certificates and deficiencies are one-to-many per inspection.
- Every field is text (the sheet's own type column says "tekst"). Dates carry a trailing `Z` (for example `2020-07-23Z`).
- A third sheet, "Notes", is empty.

## XML structure (from the embedded schema)

- Namespace `urn:getPublicInspections.xmlData.business.thetis.emsa.europa.eu` (THETIS is EMSA's port state control system). The workbook's data connection names its source file `GetPublicFile_20210629_0345.xml` from the Paris MoU "DATA Exchange Service (DES)", which suggests how downloaded files are named; confirm once the account is open.
- Root `InspectionResultsReceipt` holds any number of `Inspection` elements.
- Almost every value is an XML attribute, so a plain streaming parser (Python's `xml.etree.ElementTree.iterparse`) reads it; no extra library is needed.

```
InspectionResultsReceipt
  Inspection  @InspectionID (integer) @ReportingAuthority @PlaceOfInspection
              @DateOfFirstVisit @DateOfFinalVisit @PSCInspectionType
    ShipParticulars  @IMO
      Name | Flag | ShipType | KeelDate | CallSign | GrossTonnage   each @Value @EffectDate
    ISMCompany       @IMO @Name @Country @Address @City
    Charterers / Charterer*            @Name @Address @City @Country @Type
    ClassCertificates / ClassCertificate*
                     @IssuingAuthority @ClassStatus @DateOfStatus
    StatutoryCertificates / StatutoryCertificate*
                     @CertificateCode (integer) @IssuingAutorityType @IssuingAuthority
                     @DateOfIssue @DateOfExpiry @DateOfLastSurvey @PlaceOfLastSurvey
                     @SurveyingAuthorityType @SurveyingAuthority
    Deficiencies / Deficiency*
                     @DefectiveItemCode @NatureOfDefectCode
                     @isGroundDetention @isRORelated @isAccidentalDamage (booleans)
    Detention        @Type @DurationOfDetention (integer)
```

Every child element is optional (`minOccurs="0"`, `nillable="true"`), so the parser must cope with any of them missing. `IssuingAutorityType` is misspelt in the schema itself; use it as spelt.

## Fields, by the suggested tables

**Inspection (one per inspection)**

| XML field | Meaning |
| --- | --- |
| `InspectionID` | Inspection id |
| `ReportingAuthority` | Inspecting state, two-letter code |
| `PlaceOfInspection` | Port, UN/LOCODE (five letters) |
| `DateOfFirstVisit`, `DateOfFinalVisit` | Dates; the first visit is the primary date |
| `PSCInspectionType` | `INITIAL_INSPECTION`, `DETAILED_INSPECTION` or `EXPANDED_INSPECTION` |
| `IMO`, `Value` (ship name), `Value8` (call sign) | Ship identity |
| `Value2` | Flag, two-letter code |
| `Value4` | Ship type, numeric code |
| `Value6` | Keel-laid date (gives ship age) |
| `Value10` | Gross tonnage |
| `EffectDate`, `EffectDate3` ... `EffectDate11` | Date each ship particular took effect |
| `IMO12`, `Name`, `Country`, `Address`, `City` | Company (ISM) |
| `Name13`, `Address14`, `City15`, `Country16`, `Type` | Charterer, with charter type |
| `Type18`, `DurationOfDetention` | Detention type and duration (one per inspection, despite sitting at the end) |

**Class certificates (many per inspection):** `IssuingAuthority` (numeric code), `ClassStatus`, `DateOfStatus`.

**Statutory certificates (many per inspection):** `CertificateCode`, issuing and surveying authority and type, dates of issue, expiry and last survey, place of last survey.

**Deficiencies (many per inspection):**

| XML field | Meaning |
| --- | --- |
| `DefectiveItemCode` | Five-digit defective item code |
| `NatureOfDefectCode` | Four-digit nature of defect code |
| `isGroundDetention` | Whether it was a ground for detention |
| `isRORelated` | Whether the recognised organisation was responsible |
| `isAccidentalDamage` | Whether it was accidental damage |

## What this means for stage 4

1. **Codes only, no free text.** A deficiency is a defective item code and a nature of defect code. Nothing describes what was actually wrong. Under rule 0 the tool never describes a deficiency beyond the official wording of its codes.
2. **Lookup tables are needed and are not in the sample:** defective item codes (the "Deficiency Codes" page on the Paris MoU site), nature of defect codes, ship type codes, recognised organisation codes and port LOCODEs. Each must come from a published source, credited.
3. **The planned summaries are all possible:** by ship type, port and deficiency, plus flag, ship age (from the keel date), inspection type, detention grounds and RO-related deficiencies.
4. **Store less than the file holds.** The file names ships, companies and charterers, with addresses. Following section 11 of the spec (no ship or company names stored or shown), ingest keeps only: inspection id, authority, port, dates, inspection type, flag, ship type code, keel date, gross tonnage, deficiency codes with their three flags, and detention type and duration. Ship name, call sign, IMO numbers, company and charterer details are dropped at ingest, and the raw XML stays in the git-ignored `data/` folder.
5. **Still to see, once the account is open:** how the download delivers files (one file per period or a running feed, and the real file names), how far back the data goes, and what the API offers.

## Lookup tables: what the published documents give (checked 3 Nov 2026)

Varun downloaded 33 public Paris MoU documents (the deficiency code list and the PSC Committee instructions and circulars) and the Paris MoU Annual Report 2025 (published 1 July 2026). None is committed; they are re-fetched from the Paris MoU site when needed.

| Code in the data | Published source | Result |
| --- | --- | --- |
| Defective item code (`DefectiveItemCode`) | "List of Deficiency Codes including Statutory Certificates", 1 July 2026 (25 pages) | **Covered.** 517 codes, each with its official wording and, where one applies, the statutory certificate it relates to. All 70 distinct codes in the public sample are in the list. Codes fall in families 01 to 19 and 99, but the list itself prints no family names. The Annual Report 2025 groups deficiencies differently, by "Deficiencies Main Group" named after the convention and chapter (for example "SOLAS chapter II-2", "MLC, 2006 Title 4", "Load Lines"), and publishes no mapping from codes to those groups. So the tool groups by neither until a published mapping is found |
| Nature of defect code (`NatureOfDefectCode`, for example `1011`) | None of the 33 documents. The abbreviation "NoD" is defined, and the ISM instruction shows one in words ("Not as required"), but no code list | **Gap.** 17 distinct codes in the sample, none explained. Not shown until a published list is found |
| Ship type code (`ShipType`, for example `340`) | None of the 33 documents. The Annual Report 2025 (table "Inspections and detentions 2025 per ship type") names the 28 ship types the Paris MoU reports by, from "Bulk carrier" to "Tug", but gives no codes | **Gap: names known, codes not.** 15 distinct codes in the sample. The code-to-name mapping must come from a published source or the Secretariat; it is never inferred from patterns in the data |
| Class society code (`IssuingAuthority`, for example `278`) | PSCC instruction 57/2024/03 lists recognised organisations by name and acronym, with no numeric codes | **Gap.** Not needed for the first summaries |
| Port (`PlaceOfInspection`) | UN/LOCODE (UNECE) | Not yet fetched; terms to check |
| Flag, inspecting state | ISO 3166 two-letter codes | Standard |

## How codes are handled (rule 0)

1. Summaries are counts from queries. The model never produces a number.
2. A code is shown with its official wording exactly as published, with the list's name, date and link.
3. A code with no published wording is shown as the bare code with "description not found". It is never guessed, and ingest counts and lists unknown codes.
4. Codes are grouped only where a published source groups them.
5. Lookup lists are fetched into the git-ignored `data/` folder with their source link and date; the public repo holds only the fetch script.
6. For the gaps (ship type codes, nature of defect codes, codes to main groups): the Annual Report 2025 was checked and has none of them. Next look in the account's download documentation and the EMSA THETIS public pages; if nothing is published, ask the Secretariat once, politely (their form terms say they give no support on interpreting the data, so it is a request, not an entitlement).
7. The PSC Committee instructions (for example the expanded-inspection items per ship type) could later be a cited source for "what inspectors look at". Check their terms of use before quoting them in the tool.

## Leads found by web search (3 Nov 2026)

1. **Background tables.** Search results describe a page, parismou.org/datasharing/ ("Datasharing Files"), listing the `GetPublicFile` history files (each about 365 days of data, zipped by year or quarter) and a zip "containing the tables necessary for converting the used codes into the XML file", updated yearly on 1 July: "Background Tables 01102026.zip", plus a separate "Public Background Tables" file. Checked 3 Nov 2026: both downloads (dated 01112026) return the site's login page instead of a zip, so both need an account. They come with the account.
2. **Paris MoU Data Exchange Service (DES) API**, "API documentation v1_0" (status "concept", 11 Feb 2021). Published by the Paris MoU at parismou.org/system/files/2021-10/API%20documentation%20v1_0.pdf; the copy read here came from a third-party research repository (github.com/dimkostir/Optimization-of-the-Concentrated-Inspection-Campaign-Model-to-Strengthen-Port-State-Control-). A REST API at `https://fileserver.parismou.org/api/`:
   - `<API KEY>/getauthorizationtoken` returns an access token valid for 10 minutes; the API key is shown with the account data on the website.
   - `<TOKEN>/getfilelist` returns the names of all files on the server.
   - `<TOKEN>/getfile/<name>` downloads one file; the example name is `SomeFile_20201102_0682.xml.zip`, so files arrive zipped.
   - The documentation's own example keeps a local folder and downloads only files not already present, which matches the polite downloader in section 6 above. Check the current version once the account is open.
3. **A ship type code table**, `ship_types_.csv` in the same repository, gives 43 codes with description, abbreviation, effect and expiry dates and four flags (targeted ship type, ro-pax, passenger, tanker). Its descriptions match the 28 ship type names in the Annual Report 2025, and all 15 codes in the public sample are in it. The repository does not say where the table came from, so it is a lead only: the tool uses the official background table from the account, and this one serves only to cross-check it.
4. **Nature of defect wording** appears in deficiency code documents of other regimes (a Mediterranean MoU THETIS-Med list and a Turkish port authority manual). These are not Paris MoU sources and are not used.

None of these files is committed.
5. **Deficiency families with names.** The Paris MoU's "Overview of Deficiency Codes" PDFs (for example the 1 July 2025 edition, parismou.org/system/files/2025-07/) and an older "2014-07-01 THETIS Deficiencies" PDF reportedly print two-digit area headings with names (for example "01 Certificates & Documentation"). If so, they give official names for the code families 01 to 19, which the 2026 list lacks. Checked 3 Nov 2026: the 1 July 2025 link now returns "page not found" (the site has been rebuilt), so older editions are not reachable at their old addresses.
