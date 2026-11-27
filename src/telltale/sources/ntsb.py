"""US NTSB marine investigation reports, listed from a CAROL export and each investigation page.

CAROL's search API needs a subscription key, and the key inside NTSB's own web app must never be
borrowed. So the listing reads a JSON file of CAROL results saved once in a browser (Marine mode,
no other filter), and asks www.ntsb.gov only for each in-scope investigation's page: CAROL gives
no title and no PDF link, and the page has both.

Two layouts of that file are read, record by record, into one shape (`_Investigation`):
- the search results the CAROL page loads (`{"results": [...], "paging": {...}}`, camelCase:
  `ntsbNumber`, `status`, `reportType`, `vehicles` as one "Name Type,  Name Type" string,
  `publishedDate` month first);
- the page's "Download Data (JSON)" file (a bare list, PascalCase: `NTSBNumber`,
  `CompletionStatus`, `ReportType`, `IsStudy`, `OriginalPublishedDate` and `EventDateTimeUTC`
  year first, and `Vehicles`, a list of vehicles each with `VehicleIds` holding `VesselType`).
  `Vehicles` and `VehicleIds` are JSON lists in the download; a Python-repr string of one is read
  too, with `ast.literal_eval` only.

Which investigations are kept, in this order (docs/sources/2026-11-27-atsb-ntsb-tsib-listings.md):
ongoing ones have no report; completed ones whose report type is a closeout memo, `None` or empty
have no report; a safety study is not a single casualty (a `Report` numbered `SA...` in the search
results, `IsStudy` in the download). Of the single-casualty reports left, one is kept when at
least one of its vessels is a merchant type. CAROL gives no tonnage, so the vessel type decides.
"""

import ast
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from telltale.http import describe, get
from telltale.models import ReportMeta
from telltale.sources.common import GROUP_REASONS, ListingError, SkippedReport, skip

SOURCE = "ntsb"
PAGE_URL = "https://www.ntsb.gov/investigations/Pages/{number}.aspx"
# Report types of a single-casualty report: a Marine Accident Report, or a brief.
REPORT_TYPES = {"Report", "Board-approved brief", "Director-approved brief"}
NO_REPORT_TYPES = {"Closeout memo", "None", ""}
# Vessel types CAROL writes at the end of each vessel in `vehicles`, with the group each belongs
# to. They are matched longest first, so "Cargo, Dry Bulk" is read before "Cargo".
VESSEL_TYPES = {
    "Cargo, Liquid Bulk": "merchant",
    "Cargo, Dry Bulk": "merchant",
    "Cargo, General": "merchant",
    "Specialty/Other": "merchant",
    "Towing/Barge": "merchant",
    "Offshore": "merchant",
    "Passenger": "merchant",
    "Cargo": "merchant",
    "Combatant/Military": "naval",
    "Patrol/Small Craft": "naval",
    "Fishing": "fishing",
    "Yacht": "recreational",
}
# A known vessel type by its lower-case spelling ("Towing/barge" occurs as well as "Towing/Barge").
# The download's `VesselType` "Other" is not here: it marks things that are not vessels (a bridge,
# a platform, a pipeline), as the free-text "... Other" does in the search results.
_KNOWN_TYPES = {vessel_type.lower(): vessel_type for vessel_type in VESSEL_TYPES}
# The accident report of an investigation whose page lists more than one PDF, by investigation
# number: the file name of that report. No naming rule picks it (Dali's report is an MIR file like
# its safety document), so each was chosen by reading the PDF covers on 27 Nov 2026.
CHOSEN_PDF: dict[str, str] = {
    # Dali and the Francis Scott Key Bridge: MIR-25-40, the accident report (259 pages), not
    # MIR-25-10, the safety document on bridge vulnerability (MIR2510.pdf).
    "DCA24MM031": "MIR2540.pdf",
    # El Faro: NTSB/MAR-17/01, the Marine Accident Report (300 pages), not the safety
    # recommendation report MSR1702.pdf or the illustrated digest SPC1801.pdf.
    "DCA16MM001": "MAR1701.pdf",
}
# www.ntsb.gov sits behind Cloudflare; the research asked one page every 1.5 seconds.
PAUSE_SECONDS = 1.5
# This many investigation pages failing in a row is a block or an outage, not one bad page.
MAX_FAILED_PAGES_IN_A_ROW = 3

logger = logging.getLogger(__name__)

# Tests replace this so they never wait.
_sleep = time.sleep


def parse_vehicles(vehicles: str) -> list[tuple[str, str | None]]:
    """(name, type) for each vessel in CAROL's `vehicles` string, in order.

    Vessels are separated by a comma and two spaces; a type can hold a comma and one space.
    Nothing separates a name from its type, so the type is read by matching the end of each part
    against the known types, ignoring case. A part with no known type (a bare name, or free-text
    "... Other", which CAROL uses for bridges and pipelines) gives the type None.
    """
    vessels: list[tuple[str, str | None]] = []
    for part in vehicles.split(",  "):
        part = part.strip()
        if not part:
            continue
        for vessel_type in sorted(VESSEL_TYPES, key=len, reverse=True):
            if part.lower().endswith(vessel_type.lower()):
                name = part[: -len(vessel_type)]
                if not name or name.endswith(" "):
                    vessels.append((name.strip(), vessel_type))
                    break
        else:
            vessels.append((part, None))
    return vessels


def _group(vessel_type: str) -> str:
    """The group of a vessel type: its own, or merchant for any other type starting "Cargo"
    (a subtype CAROL may add, such as "Cargo, Container")."""
    if vessel_type not in VESSEL_TYPES and vessel_type.lower().startswith("cargo"):
        return "merchant"
    return VESSEL_TYPES[vessel_type]


def scope_reason(vessel_types: list[str]) -> str | None:
    """None when a vessel type is merchant, else why the investigation is out of scope."""
    if not vessel_types:
        return "no vessel listed"
    groups = {_group(t) for t in vessel_types}
    if "merchant" in groups:
        return None
    if len(groups) == 1:
        return GROUP_REASONS[groups.pop()]
    return "no merchant vessel: " + ", ".join(sorted(GROUP_REASONS[g] for g in groups))


# Tags that never hold anything and have no closing tag.
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "keygen", "link", "meta",
    "param", "source", "track", "wbr",
}
_DISPLAY_NONE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
# The map pop-up's heading. It is never the title, however the pop-up is hidden.
_MAP_HEADING = "accident location"


def _hidden(attributes: dict[str, str | None]) -> bool:
    """True for the map pop-up (id `modal_map`) or any element styled `display:none` inline."""
    return (attributes.get("id") or "").strip() == "modal_map" or bool(
        _DISPLAY_NONE.search(attributes.get("style") or "")
    )


class _InvestigationPage(HTMLParser):
    """The first <h1> not inside a hidden element (the title) and the links in the page's
    `reports-feed` list.

    Real pages put a hidden map pop-up, headed "<h1>Accident Location</h1>", before the title.
    Open elements are kept on a stack, so the hidden one's closing tag (or its parent's, when it
    is left unclosed) ends the skip; a void tag is never pushed, so it cannot leave it stuck on.
    A heading reading "Accident Location" is passed over even when the pop-up is hidden some
    other way (a class, a renamed id), and the next h1 is read instead.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.report_links: list[str] = []
        self._title_parts: list[str] | None = None
        self._feed_depth = 0
        self._open: list[str] = []
        # The stack position of the outermost hidden element, while inside one.
        self._hidden_at: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag not in _VOID_TAGS:
            if self._hidden_at is None and _hidden(attributes):
                self._hidden_at = len(self._open)
            self._open.append(tag)
        if tag == "h1":
            if self._hidden_at is None and self.title is None and self._title_parts is None:
                self._title_parts = []
        elif tag == "ul":
            if self._feed_depth or "reports-feed" in (attributes.get("class") or "").split():
                self._feed_depth += 1
        elif tag == "a" and self._feed_depth and attributes.get("href"):
            self.report_links.append(attributes["href"])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # A browser ignores the slash on a non-void tag: `<div .../>` opens a div. Read it the
        # same way, so a self-closed hidden element hides what follows until its closing tag.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._open:
            # Close the latest open element of this tag and anything left unclosed inside it.
            del self._open[len(self._open) - 1 - self._open[::-1].index(tag) :]
            if self._hidden_at is not None and len(self._open) <= self._hidden_at:
                self._hidden_at = None
        if tag == "h1" and self._title_parts is not None:
            text = " ".join("".join(self._title_parts).split())
            if text.casefold() != _MAP_HEADING:
                self.title = text
            self._title_parts = None
        elif tag == "ul" and self._feed_depth:
            self._feed_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._title_parts is not None and self._hidden_at is None:
            self._title_parts.append(data)


def read_investigation_page(html: str, page_url: str) -> tuple[str | None, list[str]]:
    """(title or None, report PDF URLs) from an investigation page.

    The PDFs are the links to a .pdf in the page's reports list, made absolute, each once, in
    page order.
    """
    parser = _InvestigationPage()
    parser.feed(html)
    parser.close()
    pdfs = [
        urljoin(page_url, href)
        for href in parser.report_links
        if href.split("?", 1)[0].split("#", 1)[0].lower().endswith(".pdf")
    ]
    return parser.title or None, list(dict.fromkeys(pdfs))


def _iso_date(value: object, layout: str) -> str | None:
    """`value` read with `layout` as YYYY-MM-DD, or None when it is missing or not that layout."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), layout).date().isoformat()
    except ValueError:
        return None


def _read_export(export_path: Path) -> list:
    try:
        data = json.loads(export_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ListingError(f"cannot read the CAROL export {export_path}: {error}") from error
    results = data.get("results") if isinstance(data, dict) else data
    if not isinstance(results, list):
        raise ListingError(f"the CAROL export {export_path} has no list of results")
    paging = data.get("paging") if isinstance(data, dict) else None
    total = paging.get("totalCount") if isinstance(paging, dict) else None
    if isinstance(total, int) and total > len(results):
        raise ListingError(
            f"the CAROL export {export_path} holds {len(results)} of {total} results; "
            "save the whole search, not one page of it"
        )
    return results


class _UnreadableVessels(ValueError):
    """A download record's `Vehicles` (or a `VehicleIds` in it) is not a readable list."""


@dataclass
class _Investigation:
    """One export record, whichever layout it came in."""

    number: object
    status: object
    # A str, or what the record held instead; a missing or null type is "".
    report_type: object
    study: bool
    # The known vessel types, each once, in order; read only when the scope rule needs them.
    vessel_types: Callable[[], list[str]]
    published: str | None
    published_raw: object
    occurred: str | None
    # The casualty's date and time (YYYY-MM-DDTHH:MM:SS), or its date alone, for ordering.
    event_time: str | None


def _read_record(fields: dict) -> _Investigation:
    """The record in the shared shape; a record with `NTSBNumber` is in the download's layout."""
    if "NTSBNumber" in fields:
        study = fields.get("IsStudy")
        event = fields.get("EventDateTimeUTC")
        return _Investigation(
            number=fields.get("NTSBNumber"),
            status=fields.get("CompletionStatus"),
            report_type=fields.get("ReportType") or "",
            study=study is True or (isinstance(study, str) and study.strip().lower() == "true"),
            vessel_types=lambda: _download_vessel_types(fields.get("Vehicles")),
            published=_iso_date(fields.get("OriginalPublishedDate"), "%Y-%m-%d"),
            published_raw=fields.get("OriginalPublishedDate"),
            occurred=_iso_date(
                event.split(" ", 1)[0] if isinstance(event, str) else None, "%Y-%m-%d"
            ),
            event_time=_iso_time(event, "%Y-%m-%d %H:%M:%S"),
        )
    report_type = fields.get("reportType") or ""
    event = fields.get("eventDateUtc")
    return _Investigation(
        number=fields.get("ntsbNumber"),
        status=fields.get("status"),
        report_type=report_type,
        study=report_type == "Report"
        and str(fields.get("reportNumber") or "").upper().startswith("SA"),
        vessel_types=lambda: _search_vessel_types(fields.get("vehicles")),
        published=_iso_date(fields.get("publishedDate"), "%m/%d/%Y"),
        published_raw=fields.get("publishedDate"),
        occurred=_iso_date(event.split(" ", 1)[0] if isinstance(event, str) else None, "%m/%d/%Y"),
        event_time=_iso_time(event, "%m/%d/%Y %I:%M:%S %p"),
    )


def _iso_time(value: object, layout: str) -> str | None:
    """`value` read with `layout` as YYYY-MM-DDTHH:MM:SS, else its date alone, else None."""
    if isinstance(value, str) and value.strip():
        try:
            return datetime.strptime(value.strip(), layout).isoformat()
        except ValueError:
            pass
        return _iso_date(value.strip().split(" ", 1)[0], layout.split(" ", 1)[0])
    return None


def _newest_first(record: "_Investigation") -> tuple[bool, str, str]:
    """Sort key, used in reverse: no published date first (ongoing investigations, the newest),
    then the latest published, then the latest casualty."""
    return record.published is None, record.published or "", record.event_time or ""


def _search_vessel_types(vehicles: object) -> list[str]:
    """The known vessel types in a search result's `vehicles` string, each once, in order."""
    types = [t for _, t in parse_vehicles(vehicles if isinstance(vehicles, str) else "") if t]
    return list(dict.fromkeys(types))


def _literal_list(value: object, name: str) -> list:
    """`value` as a list: a JSON list as it is, a Python-repr string of one read with
    `ast.literal_eval` (never `eval`), null or a blank string as no items."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return []
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as error:
            raise _UnreadableVessels(f"{name} is not a list: {type(error).__name__}") from error
    if not isinstance(value, list):
        raise _UnreadableVessels(f"{name} is a {type(value).__name__}, not a list")
    return value


def _download_vessel_types(vehicles: object) -> list[str]:
    """The known `VesselType`s of a download record's vehicles, each once, in order, in their
    known spelling (matched ignoring case); any other type starting "Cargo" as written. Raises
    _UnreadableVessels when the lists cannot be read."""
    types: list[str] = []
    for vehicle in _literal_list(vehicles, "Vehicles"):
        if not isinstance(vehicle, dict):
            raise _UnreadableVessels(f"a vehicle is a {type(vehicle).__name__}, not a record")
        for vessel in _literal_list(vehicle.get("VehicleIds"), "VehicleIds"):
            if not isinstance(vessel, dict):
                raise _UnreadableVessels(f"a vessel is a {type(vessel).__name__}, not a record")
            vessel_type = vessel.get("VesselType")
            if not isinstance(vessel_type, str):
                continue
            vessel_type = vessel_type.strip()
            if vessel_type.lower() in _KNOWN_TYPES:
                types.append(_KNOWN_TYPES[vessel_type.lower()])
            elif vessel_type.lower().startswith("cargo"):
                types.append(vessel_type)
    return list(dict.fromkeys(types))


def _out_of_scope(record: _Investigation) -> tuple[str, bool] | None:
    """(reason, dropped) when the record is left out before its page is asked for."""
    status, report_type = record.status, record.report_type
    if status == "Ongoing":
        return "ongoing: no report yet", False
    if status != "Completed":
        return f"status {status!r}", True
    if not isinstance(report_type, str):
        return f"report type {report_type!r}", True
    if report_type in NO_REPORT_TYPES:
        return f"no report: {report_type or 'no report type'}", False
    if report_type not in REPORT_TYPES:
        return f"report type {report_type!r}", True
    if record.study:
        return "safety study, not a single casualty", False
    try:
        reason = scope_reason(record.vessel_types())
    except _UnreadableVessels as error:
        return f"vessel list cannot be read: {error}", True
    return None if reason is None else (reason, False)


def _chosen_pdf(number: str, pdf_urls: list[str]) -> tuple[str | None, str | None]:
    """(pdf_url, reason) for a page listing more than one PDF: the chosen report when exactly one
    listed PDF's file name matches CHOSEN_PDF (ignoring case), else a reason to leave it out."""
    chosen = CHOSEN_PDF.get(number.upper())
    if chosen is None:
        return None, "investigation page lists more than one PDF"
    matches = [
        url for url in pdf_urls
        if urlsplit(url).path.rsplit("/", 1)[-1].lower() == chosen.lower()
    ]
    if not matches:
        reason = f"investigation page lists more than one PDF, and not the chosen report {chosen}"
        return None, reason
    if len(matches) > 1:
        return None, f"investigation page lists the chosen report {chosen} at more than one link"
    return matches[0], None


def list_reports(
    client: httpx.Client,
    *,
    export_path: Path,
    limit: int | None = None,
    on_skip: Callable[[SkippedReport], None] | None = None,
) -> list[ReportMeta]:
    """Single-casualty reports with a merchant vessel, newest published first (then latest
    casualty), whatever the export's order, so `limit` takes the most recent.

    Every result left out is passed to `on_skip`, in that order: expected filters (ongoing, no
    report, safety study, no merchant vessel), and (logged as a warning) results with no
    investigation id, an unknown status or report type, a vessel list that cannot be read, no
    published date, or whose page failed after retries, has no title, or links no report PDF or
    more than one. A page with more than one PDF is kept only when its investigation is in
    CHOSEN_PDF and exactly one of its PDFs is the chosen file. An investigation id already seen
    is skipped without a call. A missing or unreadable export raises ListingError naming the
    file, and so do three failed pages in a row, naming the last page.
    """
    reports: list[ReportMeta] = []
    seen: set[str] = set()
    page_calls = 0
    failed_in_a_row = 0

    records = [
        (position, _read_record(result if isinstance(result, dict) else {}))
        for position, result in enumerate(_read_export(export_path), start=1)
    ]
    # A stable sort: records that tie keep the export's order.
    records.sort(key=lambda item: _newest_first(item[1]), reverse=True)

    for position, record in records:
        if limit is not None and len(reports) >= limit:
            break
        number = record.number
        if not isinstance(number, str) or not re.fullmatch(r"[A-Za-z0-9]+", number.strip()):
            reason = "export result has no investigation id"
            skip(on_skip, SkippedReport("", f"export result {position}", "", reason, True), logger)
            continue
        number = number.strip().upper()
        if number in seen:
            continue
        seen.add(number)
        slug = f"{SOURCE}-{number.lower()}"
        page_url = PAGE_URL.format(number=number)

        left_out = _out_of_scope(record)
        if left_out is not None:
            reason, dropped = left_out
            skip(on_skip, SkippedReport(slug, number, page_url, reason, dropped), logger)
            continue
        published = record.published
        if published is None:
            reason = f"no published date: {record.published_raw!r}"
            skip(on_skip, SkippedReport(slug, number, page_url, reason, True), logger)
            continue

        if page_calls:
            _sleep(PAUSE_SECONDS)
        page_calls += 1
        try:
            html = get(client, page_url).text
        except httpx.HTTPError as error:
            failed_in_a_row += 1
            if failed_in_a_row >= MAX_FAILED_PAGES_IN_A_ROW:
                raise ListingError(
                    f"{failed_in_a_row} NTSB investigation pages in a row could not be read; "
                    f"the last, {page_url}: {describe(error)}"
                ) from error
            reason = f"investigation page could not be read: {describe(error)}"
            skip(on_skip, SkippedReport(slug, number, page_url, reason, True), logger)
            continue
        failed_in_a_row = 0
        title, pdf_urls = read_investigation_page(html, page_url)
        reason = None
        pdf_url = pdf_urls[0] if len(pdf_urls) == 1 else None
        if title is None:
            reason = "investigation page has no title"
        elif not pdf_urls:
            reason = "investigation page links no report PDF"
        elif len(pdf_urls) > 1:
            pdf_url, reason = _chosen_pdf(number, pdf_urls)
        if reason is not None:
            skip(on_skip, SkippedReport(slug, title or number, page_url, reason, True), logger)
            continue

        reports.append(
            ReportMeta(
                slug=slug,
                title=title,
                page_url=page_url,
                published=published,
                occurred=record.occurred,
                vessel_types=record.vessel_types(),
                report_type=record.report_type,
                pdf_url=pdf_url,
                source=SOURCE,
            )
        )

    return reports
