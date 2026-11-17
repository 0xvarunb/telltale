"""UK MAIB merchant-ship investigation reports, listed through the GOV.UK APIs."""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from telltale.http import describe, get
from telltale.models import ReportMeta

SEARCH_URL = "https://www.gov.uk/api/search.json"
CONTENT_URL = "https://www.gov.uk/api/content"
SITE_URL = "https://www.gov.uk"
SEARCH_FILTERS = {
    "filter_format": "maib_report",
    "filter_vessel_type": "merchant-vessel-100-gross-tons-or-over",
    "fields": "title,link,public_timestamp",
}
INVESTIGATION_REPORT = "investigation-report"
# MAIB report types the tool keeps (Ruling 23), with the label shown before a report's title so a
# reader never mistakes a bulletin or a preliminary examination for a full investigation.
KEPT_REPORT_TYPES = {
    INVESTIGATION_REPORT: "",
    "investigation-report-on-behalf-of-red-ensign-group": "Red Ensign Group investigation",
    "safety-bulletin": "Safety bulletin",
    "completed-preliminary-examination": "Preliminary examination",
}
PAUSE_SECONDS = 0.2

logger = logging.getLogger(__name__)

PDF_LINK = re.compile(
    r"https://assets\.(?:publishing\.service|digital\.cabinet-office)\.gov\.uk/[^\s\"'<>]+?\.pdf",
    re.IGNORECASE,
)
# Attachments that sit beside a report rather than being it. Older GOV.UK pages give no file
# sizes, so "the largest PDF" alone picked the one-page synopsis for 163 reports (4 Nov 2026).
SECONDARY_ATTACHMENT = re.compile(
    r"synopsis|summary|annex|appendi|flyer|leaflet|poster|press|corrigend|errat|recommendation",
    re.IGNORECASE,
)

# Tests replace this so they never wait.
_sleep = time.sleep


class ListingError(Exception):
    """The search API answered with something the listing cannot page through."""


@dataclass
class SkippedReport:
    """A search result left out of the listing.

    `dropped` is True when it may be an investigation report that could not be read (no report
    type, or its content request failed); False for another report type such as a safety
    bulletin, which is an expected filter.
    """

    slug: str
    title: str
    page_url: str
    reason: str
    dropped: bool


def _get_json(client: httpx.Client, url: str, params: dict | None = None) -> dict:
    return get(client, url, params).json()


def _slug(link: str) -> str:
    return link.rstrip("/").rsplit("/", 1)[-1]


def pick_pdf_url(content: dict) -> str | None:
    """The report PDF, else the first PDF link in the body.

    Among the PDF attachments: not a synopsis, summary or annex first, then one named as a
    report, then the largest; on a full tie, the first listed.
    """
    details = content.get("details") or {}
    pdfs = [
        attachment
        for attachment in details.get("attachments") or []
        if attachment.get("url")
        and (
            attachment.get("content_type") == "application/pdf"
            or attachment["url"].lower().endswith(".pdf")
        )
    ]
    if pdfs:

        def rank(attachment: dict) -> tuple[bool, bool, int]:
            name = " ".join(
                str(attachment.get(key) or "") for key in ("title", "filename", "url")
            )
            return (
                not SECONDARY_ATTACHMENT.search(name),
                "report" in name.lower(),
                attachment.get("file_size") or 0,
            )

        return max(pdfs, key=rank)["url"]

    match = PDF_LINK.search(details.get("body") or "")
    return match.group(0) if match else None


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} is a {type(value).__name__}, not text")
    return value


def _to_report(result: dict, content: dict) -> ReportMeta:
    """Raises AttributeError, KeyError, TypeError or ValueError on a malformed result or body."""
    link = _text(result["link"], "link")
    metadata = (content.get("details") or {}).get("metadata") or {}
    occurred = metadata.get("date_of_occurrence")
    vessel_types = metadata.get("vessel_type") or []
    pdf_url = pick_pdf_url(content)
    body = (content.get("details") or {}).get("body")
    return ReportMeta(
        slug=_slug(link),
        title=_text(result["title"], "title"),
        page_url=SITE_URL + link,
        published=_text(result["public_timestamp"], "public_timestamp"),
        occurred=None if occurred is None else _text(occurred, "date_of_occurrence"),
        vessel_types=[vessel_types] if isinstance(vessel_types, str) else list(vessel_types),
        report_type=_text(metadata.get("report_type") or "", "report_type"),
        pdf_url=pdf_url,
        # A report with no PDF (every completed preliminary examination) is the page text.
        page_html=_text(body, "body") if pdf_url is None and body else None,
    )


def _skip(on_skip: Callable[[SkippedReport], None] | None, skipped: SkippedReport) -> None:
    if skipped.dropped:
        logger.warning(
            "left out %s (%s): %s",
            skipped.slug or skipped.title,
            skipped.reason,
            skipped.page_url or "no page link",
        )
    else:
        logger.debug("left out %s (%s)", skipped.slug, skipped.reason)
    if on_skip is not None:
        on_skip(skipped)


def list_merchant_reports(
    client: httpx.Client,
    page_size: int = 100,
    limit: int | None = None,
    on_skip: Callable[[SkippedReport], None] | None = None,
) -> list[ReportMeta]:
    """Investigation reports on merchant vessels of 100 gross tons or over, in search order.

    Every search result left out is passed to `on_skip`: other report types, and (logged as a
    warning) results with no report type, with no link (no page link to give), whose content
    request failed after retries or returned something other than a JSON object, or whose
    result or content is malformed. A slug already seen on an earlier page is skipped without
    a call. A failed search request raises, as does a search response with no `total`.
    """
    reports: list[ReportMeta] = []
    seen: set[str] = set()
    content_calls = 0
    start = 0

    while limit is None or len(reports) < limit:
        page = _get_json(
            client, SEARCH_URL, {**SEARCH_FILTERS, "count": page_size, "start": start}
        )
        if not isinstance(page.get("total"), int):
            raise ListingError(f"GOV.UK search response at start={start} has no total")
        results = page.get("results") or []

        for position, result in enumerate(results, start=start + 1):
            if limit is not None and len(reports) >= limit:
                break
            fields = result if isinstance(result, dict) else {}
            link, title = fields.get("link"), fields.get("title")
            if not isinstance(link, str) or not link.strip("/"):
                # No page link to give: name it by its title, else its place in the search.
                name = title if isinstance(title, str) and title else f"search result {position}"
                _skip(on_skip, SkippedReport("", name, "", "search result has no link", True))
                continue
            slug = _slug(link)
            page_url = SITE_URL + link
            name = title if isinstance(title, str) and title else slug
            if slug in seen:
                continue
            seen.add(slug)
            if content_calls:
                _sleep(PAUSE_SECONDS)
            content_calls += 1
            try:
                content = _get_json(client, CONTENT_URL + link)
                if not isinstance(content, dict):
                    raise ValueError(f"content is a JSON {type(content).__name__}, not an object")
            except (httpx.HTTPError, ValueError) as error:
                # ValueError covers a 200 whose body is not JSON.
                why = describe(error) if isinstance(error, httpx.HTTPError) else str(error)
                reason = f"content request failed: {why}"
                _skip(on_skip, SkippedReport(slug, name, page_url, reason, dropped=True))
                continue
            try:
                report = _to_report(result, content)
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                # A malformed search result or content body drops this report, not the listing.
                reason = f"content could not be read: {type(error).__name__}: {error}"
                _skip(on_skip, SkippedReport(slug, name, page_url, reason, dropped=True))
                continue
            if report.report_type in KEPT_REPORT_TYPES:
                reports.append(report)
                continue
            reason = report.report_type or "no report type"
            skipped = SkippedReport(
                report.slug, report.title, report.page_url, reason, dropped=not report.report_type
            )
            _skip(on_skip, skipped)

        start += len(results)
        if not results or start >= page["total"]:
            break

    return reports


def labelled_title(title: str, report_type: str) -> str:
    """The title as shown in answers: prefixed with its type unless it is a full investigation."""
    label = KEPT_REPORT_TYPES.get(report_type, "")
    return f"{label}: {title}" if label else title
