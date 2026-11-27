import copy
import json
from pathlib import Path

import httpx
import pytest
import respx

from telltale.models import ReportMeta
from telltale.sources.common import ListingError, SkippedReport
from telltale.sources.tsib import LISTING_URL, find_items, list_reports

FIXTURES = Path(__file__).parent.parent / "fixtures" / "sources"
PAGE_HTML = (FIXTURES / "tsib_marine_reports_page.html").read_text()
ITEM_ID = "/what-we-do/transport-investigations/marine/marine-reports/{}"
ASSETS = "https://isomer-user-content.by.gov.sg/287/"
# The fixture splits its items across two push tags, as the real page does.
assert PAGE_HTML.count("self.__next_f.push([1,") == 2

# The fixture's five reports, in page order (newest first).
EXPECTED = [
    ReportMeta(
        slug="tsib-00000000-0000-4000-8000-000000000001",
        title="Fatal injury of test crew during synthetic hatch work",
        page_url=ASSETS + "0000000a-0000-4000-8000-00000000000a/Fatal%20injury%20of%20crew"
        "%20onboard%20TEST%20VENTURE%2C%20at%20sea%2C%203%20February%202026.pdf",
        published="",
        occurred="2026-02-03",
        vessel_types=["CS: Cargo ship"],
        report_type="Very Serious Marine Casualty",
        pdf_url=ASSETS + "0000000a-0000-4000-8000-00000000000a/Fatal%20injury%20of%20crew"
        "%20onboard%20TEST%20VENTURE%2C%20at%20sea%2C%203%20February%202026.pdf",
        source="tsib",
    ),
    ReportMeta(
        slug="tsib-00000000-0000-4000-8000-000000000002",
        title="Collision between a tanker and a test craft",
        page_url=ASSETS + "0000000b-0000-4000-8000-00000000000b/"
        "final-report---collision-between-harbour-test-and-small-test-19-aug-2025.pdf",
        published="",
        occurred="2025-08-19",
        vessel_types=["OT: Others", "TA: Tanker"],
        report_type="Very Serious Marine Casualty",
        pdf_url=ASSETS + "0000000b-0000-4000-8000-00000000000b/"
        "final-report---collision-between-harbour-test-and-small-test-19-aug-2025.pdf",
        source="tsib",
    ),
    ReportMeta(
        slug="tsib-00000000-0000-4000-8000-000000000003",
        title="Man overboard (loss of life) from test tug",
        page_url=ASSETS + "0000000c-0000-4000-8000-00000000000c/"
        "mib-mai-cas-999---test-pull-7-nov-2024---final-report.pdf",
        published="",
        occurred="2024-11-07",
        vessel_types=["TU: Tug"],
        report_type="Marine Casualty",
        pdf_url=ASSETS + "0000000c-0000-4000-8000-00000000000c/"
        "mib-mai-cas-999---test-pull-7-nov-2024---final-report.pdf",
        source="tsib",
    ),
    ReportMeta(
        slug="tsib-00000000-0000-4000-8000-000000000004",
        title="Loss of life while operating a test crane",
        page_url=ASSETS + "0000000d-0000-4000-8000-00000000000d/"
        "final-report---fatality-onboard-test-rig-one-30-may-2023.pdf",
        published="",
        occurred="2023-05-30",
        vessel_types=["OT: Others"],
        report_type="Very Serious Marine Casualty",
        pdf_url=ASSETS + "0000000d-0000-4000-8000-00000000000d/"
        "final-report---fatality-onboard-test-rig-one-30-may-2023.pdf",
        source="tsib",
    ),
    # The fifth item's id ends in a readable name, as 64 of the real page's 72 do, so its key is
    # the UUID folder of its PDF link.
    ReportMeta(
        slug="tsib-0000000e-0000-4000-8000-00000000000e",
        title="Crew missing at sea",
        page_url=ASSETS + "0000000e-0000-4000-8000-00000000000e/"
        "crew-missing-from-test-supplier-at-sea-on-2-march-2022.pdf",
        published="",
        occurred="2022-03-02",
        vessel_types=["SV: Supply vessel"],
        report_type="Marine Casualty",
        pdf_url=ASSETS + "0000000e-0000-4000-8000-00000000000e/"
        "crew-missing-from-test-supplier-at-sea-on-2-march-2022.pdf",
        source="tsib",
    ),
]
NO_REPORT_ID = "listing item has no report id (no UUID in its id or its PDF link)"


@pytest.fixture
def mock_site():
    # assert_all_mocked: any request without a matching route raises.
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


def fixture_items() -> list[dict]:
    return copy.deepcopy(find_items(PAGE_HTML, LISTING_URL))


def page_with(items: list[dict]) -> str:
    """A listing page whose Next.js data holds `items`, in one push tag."""
    payload = '76:["$","main",null,{"page":{},"items":' + json.dumps(items) + "}]\n"
    return (
        "<html><body><script>self.__next_f.push([1, "
        + json.dumps(payload)
        + "])</script></body></html>"
    )


def item(number: int, ship_types: list[str] | None, **fields) -> dict:
    """The fixture's first item as report `number`, with these ship types (None: no tag)."""
    made = fixture_items()[0]
    uuid = f"00000000-0000-4000-8000-{number:012d}"
    made["id"] = ITEM_ID.format(uuid)
    made["title"] = made["itemTitle"] = f"Synthetic casualty {number} [PDF, 1 MB]"
    made["referenceLinkHref"] = ASSETS + f"{uuid}/synthetic-{number}.pdf"
    made["tags"] = [t for t in made["tags"] if t["category"] != "Ship type"]
    if ship_types is not None:
        made["tags"].insert(0, {"id": "x", "category": "Ship type", "selected": ship_types})
    made.update(fields)
    return made


def run(router, html: str, **kwargs) -> list[ReportMeta]:
    router.get(LISTING_URL).mock(return_value=httpx.Response(200, text=html))
    with httpx.Client() as client:
        return list_reports(client, **kwargs)


def test_lists_in_scope_reports_with_their_pdf(mock_site):
    route = mock_site.get(LISTING_URL).mock(return_value=httpx.Response(200, text=PAGE_HTML))

    with httpx.Client() as client:
        reports = list_reports(client)

    assert reports == EXPECTED
    # One request, with the Telltale user agent.
    assert route.call_count == 1
    assert route.calls[0].request.headers["User-Agent"].startswith("telltale/")


def test_every_report_links_its_own_report_page(mock_site):
    reports = run(mock_site, PAGE_HTML)
    hrefs = [i["referenceLinkHref"] for i in fixture_items()]

    # There is no page per report: the report page is the report's own PDF, never the listing.
    assert [r.page_url for r in reports] == [r.pdf_url for r in reports]
    assert len({r.page_url for r in reports}) == len(reports) == len(hrefs)
    assert LISTING_URL not in {r.page_url for r in reports}
    for report, href in zip(reports, hrefs):
        assert httpx.URL(report.page_url).path == httpx.URL(href).path
        assert " " not in report.page_url and "," not in report.page_url


def test_out_of_scope_reports_are_skipped_with_a_reason(mock_site):
    items = fixture_items()[:1] + [
        item(11, ["FV: Fishing vessel"]),
        item(12, ["PC: Pleasure craft"]),
        item(13, ["NV: Naval vessel"]),
        item(14, ["FV: Fishing vessel", "NV: Naval vessel"]),
        # A merchant type keeps a collision with a fishing vessel.
        item(15, ["FV: Fishing vessel", "BA: Barge"]),
        item(16, ["SV: Supply vessel"]),
    ]
    skipped: list[SkippedReport] = []

    reports = run(mock_site, page_with(items), on_skip=skipped.append)

    assert [r.slug for r in reports] == [
        "tsib-00000000-0000-4000-8000-000000000001",
        "tsib-00000000-0000-4000-8000-000000000015",
        "tsib-00000000-0000-4000-8000-000000000016",
    ]
    assert [(s.slug, s.title, s.page_url, s.reason, s.dropped) for s in skipped] == [
        (
            f"tsib-00000000-0000-4000-8000-0000000000{n}",
            f"Synthetic casualty {n}",
            ASSETS + f"00000000-0000-4000-8000-0000000000{n}/synthetic-{n}.pdf",
            reason,
            False,
        )
        for n, reason in [
            (11, "fishing vessel"),
            (12, "recreational vessel"),
            (13, "naval vessel"),
            (14, "no merchant vessel: fishing vessel, naval vessel"),
        ]
    ]


def test_unreadable_items_are_dropped_with_a_reason(mock_site):
    no_href = item(21, ["CS: Cargo ship"])
    del no_href["referenceLinkHref"]
    items = [
        "not an item",
        item(20, None),
        no_href,
        item(22, ["XX: Something new"]),
        item(23, ["CS: Cargo ship"], tags=[{"category": "Ship type", "selected": ["TA: Tanker"]}],
             plaintextTags=[]),
        item(24, ["CS: Cargo ship"], id="/what-we-do/elsewhere",
             referenceLinkHref="https://isomer-user-content.by.gov.sg/287/files/report-24.pdf"),
    ]
    skipped: list[SkippedReport] = []

    reports = run(mock_site, page_with(items), on_skip=skipped.append)

    assert reports == []
    assert [(s.slug, s.reason, s.dropped) for s in skipped] == [
        ("", NO_REPORT_ID, True),
        ("tsib-00000000-0000-4000-8000-000000000020", "no ship type", True),
        ("tsib-00000000-0000-4000-8000-000000000021", "no report PDF link", True),
        ("tsib-00000000-0000-4000-8000-000000000022", "unknown ship type: XX: Something new",
         True),
        ("tsib-00000000-0000-4000-8000-000000000023", "no report category", True),
        ("", NO_REPORT_ID, True),
    ]


def test_a_category_in_plaintext_tags_alone_is_read(mock_site):
    only_plaintext = item(30, ["CS: Cargo ship"])
    only_plaintext["tags"] = [t for t in only_plaintext["tags"] if t["category"] != "Category"]

    reports = run(mock_site, page_with([only_plaintext]))

    assert [r.report_type for r in reports] == ["Very Serious Marine Casualty"]


def test_a_report_not_listed_as_past_is_dropped(mock_site):
    interim = item(31, ["CS: Cargo ship"])
    for tag in interim["tags"]:
        if tag["category"] == "Status":
            tag["selected"] = ["Interim Reports"]
    skipped: list[SkippedReport] = []

    reports = run(mock_site, page_with([interim]), on_skip=skipped.append)

    assert reports == []
    assert [(s.reason, s.dropped) for s in skipped] == [("status 'Interim Reports'", True)]


@pytest.mark.parametrize("status", ["Past reports", "PAST REPORTS", "past Reports"])
def test_a_past_report_status_in_any_case_is_kept(mock_site, status):
    past = item(32, ["CS: Cargo ship"])
    for tag in past["tags"]:
        if tag["category"] == "Status":
            tag["selected"] = [status]

    reports = run(mock_site, page_with([past]))

    assert [r.slug for r in reports] == ["tsib-00000000-0000-4000-8000-000000000032"]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Fatal injury [PDF, 881 KB]", "Fatal injury"),
        ("\tCollision  of two ships [PDF, 1.2 MB] ", "Collision of two ships"),
        ("No size here", "No size here"),
        ("Grounding [pdf,640kb]", "Grounding"),
    ],
)
def test_title_loses_its_file_size(mock_site, title, expected):
    reports = run(mock_site, page_with([item(40, ["TA: Tanker"], title=title)]))

    assert [r.title for r in reports] == [expected]


def test_a_missing_or_odd_date_gives_no_occurred_date(mock_site):
    items = [item(41, ["TA: Tanker"], date="$undefined"), item(42, ["TA: Tanker"])]
    del items[1]["date"]

    reports = run(mock_site, page_with(items))

    assert [r.occurred for r in reports] == [None, None]


def test_a_repeated_item_is_listed_once(mock_site):
    first = fixture_items()[0]

    reports = run(mock_site, page_with([first, copy.deepcopy(first)]))

    assert [r.slug for r in reports] == ["tsib-00000000-0000-4000-8000-000000000001"]


def test_limit_stops_after_n(mock_site):
    skipped: list[SkippedReport] = []
    items = fixture_items()
    items.insert(1, item(11, ["FV: Fishing vessel"]))
    items.insert(3, item(12, ["FV: Fishing vessel"]))

    reports = run(mock_site, page_with(items), limit=2, on_skip=skipped.append)

    assert [r.slug for r in reports] == [EXPECTED[0].slug, EXPECTED[1].slug]
    # Nothing after the second report is read, so nothing after it is reported as left out.
    assert [s.slug for s in skipped] == ["tsib-00000000-0000-4000-8000-000000000011"]


@pytest.mark.parametrize("status", [404, 403])
def test_a_failed_page_raises_listing_error_naming_the_url(mock_site, status):
    mock_site.get(LISTING_URL).mock(return_value=httpx.Response(status))

    with httpx.Client() as client, pytest.raises(ListingError) as raised:
        list_reports(client)

    assert LISTING_URL in str(raised.value)
    assert f"HTTP {status}" in str(raised.value)


@pytest.mark.parametrize(
    "html",
    [
        "<html><body><p>Moved</p></body></html>",
        # Push tags, but no items array.
        PAGE_HTML.replace('\\"items\\":[', '\\"entries\\":['),
        # An items array that is cut off.
        PAGE_HTML.split("<script>self.__next_f.push([1, \"image")[0] + "</body></html>",
        # An empty items array.
        page_with([]),
        # An items array that is not reports.
        page_with([{"label": "CS: Cargo ship", "count": 1}]),
    ],
    ids=["no-data", "no-items", "cut-off", "empty", "not-reports"],
)
def test_missing_items_array_raises_listing_error(mock_site, html):
    mock_site.get(LISTING_URL).mock(return_value=httpx.Response(200, text=html))

    with httpx.Client() as client, pytest.raises(ListingError) as raised:
        list_reports(client)

    assert LISTING_URL in str(raised.value)


READABLE_ID = ITEM_ID.format("man-overboard--loss-of-life--while-disembarking--pdf--811-kb-")


def test_an_item_with_a_readable_id_is_keyed_by_its_pdf_uuid(mock_site):
    pdf_uuid = "8231BABE-E172-433D-8A39-BF95A1F5C614"
    readable = item(
        50,
        ["TU: Tug"],
        id=READABLE_ID,
        referenceLinkHref=ASSETS + f"{pdf_uuid}/missing-of-fitter-at-sea-on-26-december-2024.pdf",
        date="$D2024-12-26T00:00:00.000Z",
    )

    reports = run(mock_site, page_with([readable]))

    pdf_url = ASSETS + f"{pdf_uuid}/missing-of-fitter-at-sea-on-26-december-2024.pdf"
    assert reports == [
        ReportMeta(
            slug=f"tsib-{pdf_uuid.lower()}",
            title="Synthetic casualty 50",
            page_url=pdf_url,
            published="",
            occurred="2024-12-26",
            vessel_types=["TU: Tug"],
            report_type="Very Serious Marine Casualty",
            pdf_url=pdf_url,
            source="tsib",
        )
    ]


def test_a_uuid_id_still_gives_the_same_slug(mock_site):
    # The 8 reports already stored keep their slugs, so a re-run updates them.
    uuid_id = item(
        51,
        ["CS: Cargo ship"],
        referenceLinkHref=ASSETS + "11111111-2222-4333-8444-555555555555/report-51.pdf",
    )

    reports = run(mock_site, page_with([uuid_id]))

    assert [r.slug for r in reports] == ["tsib-00000000-0000-4000-8000-000000000051"]


def test_two_items_with_the_same_readable_id_are_both_kept(mock_site):
    # Three different reports on the real page are titled "Man overboard (loss of life) while
    # disembarking", so the readable name is not a safe key.
    first = item(52, ["TU: Tug"], id=READABLE_ID,
                 referenceLinkHref=ASSETS + "aaaaaaaa-0000-4000-8000-000000000052/first.pdf")
    second = item(53, ["TU: Tug"], id=READABLE_ID,
                  referenceLinkHref=ASSETS + "bbbbbbbb-0000-4000-8000-000000000053/second.pdf")

    reports = run(mock_site, page_with([first, second]))

    assert [r.slug for r in reports] == [
        "tsib-aaaaaaaa-0000-4000-8000-000000000052",
        "tsib-bbbbbbbb-0000-4000-8000-000000000053",
    ]


def test_a_report_id_shared_by_a_different_pdf_is_left_out_by_name(mock_site):
    # Two PDFs in one asset folder share the folder's UUID, so the second report would be lost
    # without a trace; an exact repeat of an item (same PDF link) is still skipped quietly.
    folder = ASSETS + "cccccccc-0000-4000-8000-000000000055/"
    first = item(55, ["TU: Tug"], id=READABLE_ID, referenceLinkHref=folder + "first.pdf")
    second = item(56, ["TU: Tug"], id=READABLE_ID, referenceLinkHref=folder + "second.pdf")
    skipped: list[SkippedReport] = []

    reports = run(
        mock_site, page_with([first, copy.deepcopy(first), second]), on_skip=skipped.append
    )

    assert [r.slug for r in reports] == ["tsib-cccccccc-0000-4000-8000-000000000055"]
    assert [(s.slug, s.title, s.page_url, s.reason, s.dropped) for s in skipped] == [
        (
            "tsib-cccccccc-0000-4000-8000-000000000055",
            "Synthetic casualty 56",
            folder + "second.pdf",
            "same report id as an earlier item: cccccccc-0000-4000-8000-000000000055",
            True,
        ),
    ]


def test_an_item_with_no_uuid_anywhere_is_left_out_with_a_true_reason(mock_site):
    no_uuid = item(54, ["CS: Cargo ship"], id=READABLE_ID,
                   referenceLinkHref=ASSETS + "reports/crew-missing-at-sea.pdf")
    skipped: list[SkippedReport] = []

    reports = run(mock_site, page_with([no_uuid]), on_skip=skipped.append)

    assert reports == []
    assert [(s.slug, s.title, s.page_url, s.reason, s.dropped) for s in skipped] == [
        ("", "Synthetic casualty 54", ASSETS + "reports/crew-missing-at-sea.pdf", NO_REPORT_ID,
         True),
    ]
    assert "has no id" not in skipped[0].reason
