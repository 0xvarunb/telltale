"""Singapore TSIB marine safety investigation reports, listed from one MOT page.

The page (GovTech Isomer, Next.js) shows ten reports at a time, but pages in the browser: the
server sends every report inside the page's React data, as string parts of
`self.__next_f.push([1, "..."])` script tags. An item list can be split across two tags, so the
parts are joined in order before the `"items":[...]` array is decoded
(docs/sources/2026-11-27-atsb-ntsb-tsib-listings.md).

There is no page per report (an item's own id answers 404), so a report's page and its PDF are
the same link. The listing gives the casualty date, not the published date (that is on the PDF
cover only), and no tonnage, so the ship type decides: a report is kept when at least one of its
ship types is a merchant or working craft.
"""

import json
import logging
import re
from collections.abc import Callable
from datetime import datetime
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from telltale.http import describe, get
from telltale.models import ReportMeta
from telltale.sources.common import GROUP_REASONS, ListingError, SkippedReport, skip

SOURCE = "tsib"
LISTING_URL = "https://www.mot.gov.sg/what-we-do/transport-investigations/marine/marine-reports/"
# Ship type tags are "<code>: <label>". These codes are kept: cargo ship, tanker, supply vessel,
# tug, barge, and "others" (on this page a working craft, or the other party to a collision).
MERCHANT_CODES = {"CS", "TA", "SV", "TU", "BA", "OT"}
# Words that mark a ship type as out of scope, with the reason given. None is on the page yet.
OUT_OF_SCOPE_WORDS = {
    "fishing": ("fishing",),
    "recreational": ("recreational", "pleasure", "yacht"),
    "naval": ("naval", "navy", "warship", "military"),
}
PAST_REPORTS = "Past Reports"

_PUSH = re.compile(r"self\.__next_f\.push\((.*?)\)\s*</script>", re.DOTALL)
_ITEMS = re.compile(r'"items"\s*:\s*\[')
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_FILE_SIZE = re.compile(r"\s*\[\s*PDF\s*,?\s*[\d.,]+\s*[KMG]?B\s*\]\s*$", re.IGNORECASE)

logger = logging.getLogger(__name__)


def _page_data(html: str) -> str:
    """Every string part of the page's `self.__next_f.push([1, "..."])` tags, joined in order."""
    parts: list[str] = []
    for match in _PUSH.finditer(html):
        try:
            pushed = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(pushed, list) and len(pushed) >= 2 and isinstance(pushed[1], str):
            parts.append(pushed[1])
    return "".join(parts)


def find_items(html: str, url: str = LISTING_URL) -> list[dict]:
    """The report items in the listing page's Next.js data.

    The first `"items":[...]` array that decodes and holds a report link
    (`referenceLinkHref`). Raises ListingError naming `url` when there is none, so a rebuilt
    page fails loudly instead of listing nothing.
    """
    data = _page_data(html)
    decoder = json.JSONDecoder()
    for match in _ITEMS.finditer(data):
        try:
            items, _ = decoder.raw_decode(data, match.end() - 1)
        except ValueError:
            continue
        if isinstance(items, list) and any(
            isinstance(i, dict) and "referenceLinkHref" in i for i in items
        ):
            return items
    raise ListingError(f"no list of reports found in the page data of {url}")


def scope_reason(ship_types: list[str]) -> tuple[str, bool] | None:
    """None when a ship type is kept, else (reason, dropped).

    `dropped` is True when no ship type is known, so the report may be in scope.
    """
    if not ship_types:
        return "no ship type", True
    groups: set[str] = set()
    unknown: list[str] = []
    for ship_type in ship_types:
        code = ship_type.split(":", 1)[0].strip().upper()
        if code in MERCHANT_CODES:
            return None
        words = ship_type.lower()
        group = next(
            (g for g, marks in OUT_OF_SCOPE_WORDS.items() if any(m in words for m in marks)),
            None,
        )
        if group is None:
            unknown.append(ship_type)
        else:
            groups.add(group)
    if unknown:
        return "unknown ship type: " + ", ".join(unknown), True
    if len(groups) == 1:
        return GROUP_REASONS[groups.pop()], False
    return "no merchant vessel: " + ", ".join(sorted(GROUP_REASONS[g] for g in groups)), False


def _tag(item: dict, category: str) -> list[str] | None:
    """The `selected` values of the item's tag in `category`, from `tags` then `plaintextTags`."""
    for key in ("tags", "plaintextTags"):
        tags = item.get(key)
        for tag in tags if isinstance(tags, list) else []:
            if isinstance(tag, dict) and tag.get("category") == category:
                selected = tag.get("selected")
                if isinstance(selected, list):
                    return [s.strip() for s in selected if isinstance(s, str) and s.strip()]
    return None


def _title(item: dict) -> str:
    title = item.get("title") or item.get("itemTitle")
    if not isinstance(title, str):
        return ""
    return " ".join(_FILE_SIZE.sub("", title).split())


def _occurred(item: dict) -> str | None:
    """The casualty date (`$D2026-01-20T00:00:00.000Z`) as YYYY-MM-DD, or None."""
    value = item.get("date")
    if not isinstance(value, str) or not value.startswith("$D"):
        return None
    try:
        return datetime.fromisoformat(value[2:]).date().isoformat()
    except ValueError:
        return None


def encode_url(url: str) -> str:
    """`url` with its path percent-encoded: file names hold spaces and commas."""
    parts = urlsplit(url.strip())
    return urlunsplit(parts._replace(path=quote(parts.path, safe="/%")))


def _pdf_url(item: dict) -> str | None:
    href = item.get("referenceLinkHref")
    if not isinstance(href, str) or not href.strip():
        return None
    url = encode_url(href)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return url


def _report_id(item: dict, pdf_url: str | None) -> str | None:
    """The report's key in lower case, or None.

    The UUID that ends the item's `id` (the newest items, and the slugs already stored), else the
    UUID folder just before the file name in its PDF link: most items end their `id` in a
    readable name, and those names repeat across reports.
    """
    item_id = item.get("id")
    last = item_id.rstrip("/").rsplit("/", 1)[-1] if isinstance(item_id, str) else ""
    if _UUID.fullmatch(last):
        return last.lower()
    if pdf_url is not None:
        folders = urlsplit(pdf_url).path.split("/")[:-1]
        if folders and _UUID.fullmatch(folders[-1]):
            return folders[-1].lower()
    return None


def list_reports(
    client: httpx.Client,
    *,
    limit: int | None = None,
    on_skip: Callable[[SkippedReport], None] | None = None,
) -> list[ReportMeta]:
    """Final reports with a merchant or working craft, in the page's order (newest first).

    A report's key is the UUID that ends its listing id, else the UUID folder of its PDF link.
    Every item left out is passed to `on_skip`: expected filters (only fishing, recreational or
    naval vessels), and (logged as a warning) items with no report id (no UUID in their id or
    their PDF link), no report PDF link, no title, no ship type or one not known, no report
    category, or a status other than past reports. An exact repeat of an item (a report id
    already seen, with the same PDF link) is skipped quietly; a report id already seen with a
    different PDF link is dropped and passed to `on_skip` with that id. A failed page, or a
    page whose report list cannot be found or read, raises ListingError naming the URL.
    """
    try:
        html = get(client, LISTING_URL).text
    except httpx.HTTPError as error:
        raise ListingError(
            f"the TSIB listing {LISTING_URL} could not be read: {describe(error)}"
        ) from error

    reports: list[ReportMeta] = []
    # Each report id seen, with the PDF link it came with.
    seen: dict[str, str | None] = {}
    for position, raw in enumerate(find_items(html, LISTING_URL), start=1):
        if limit is not None and len(reports) >= limit:
            break
        fields = raw if isinstance(raw, dict) else {}
        title = _title(fields)
        pdf_url = _pdf_url(fields)
        uuid = _report_id(fields, pdf_url)
        if uuid is None:
            reason = "listing item has no report id (no UUID in its id or its PDF link)"
            label = title or f"listing item {position}"
            skip(on_skip, SkippedReport("", label, pdf_url or "", reason, True), logger)
            continue
        slug = f"{SOURCE}-{uuid}"
        label = title or uuid
        if uuid in seen:
            if seen[uuid] != pdf_url:
                reason = f"same report id as an earlier item: {uuid}"
                skip(on_skip, SkippedReport(slug, label, pdf_url or "", reason, True), logger)
            continue
        seen[uuid] = pdf_url

        reason, dropped = None, True
        ship_types = _tag(fields, "Ship type") or []
        categories = _tag(fields, "Category")
        status = _tag(fields, "Status")
        if pdf_url is None:
            reason = "no report PDF link"
        elif not title:
            reason = "no title"
        elif status and [s.casefold() for s in status] != [PAST_REPORTS.casefold()]:
            reason = f"status {', '.join(status)!r}"
        elif (left_out := scope_reason(ship_types)) is not None:
            reason, dropped = left_out
        elif not categories:
            reason = "no report category"
        if reason is not None:
            skip(on_skip, SkippedReport(slug, label, pdf_url or "", reason, dropped), logger)
            continue

        reports.append(
            ReportMeta(
                slug=slug,
                title=title,
                page_url=pdf_url,
                published="",
                occurred=_occurred(fields),
                vessel_types=ship_types,
                report_type=categories[0],
                pdf_url=pdf_url,
                source=SOURCE,
            )
        )

    return reports
