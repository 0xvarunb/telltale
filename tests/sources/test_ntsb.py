import copy
import json
from pathlib import Path

import httpx
import pytest
import respx

from telltale.models import ReportMeta
from telltale.sources import ntsb
from telltale.sources.common import ListingError, SkippedReport
from telltale.sources.ntsb import list_reports, parse_vehicles, read_investigation_page

FIXTURES = Path(__file__).parent.parent / "fixtures" / "sources"
EXPORT = FIXTURES / "ntsb_carol_investigations.json"
PAGE_HTML = (FIXTURES / "ntsb_investigation_page.html").read_text()
PAGE = "https://www.ntsb.gov/investigations/Pages/{}.aspx"
PDF = "https://www.ntsb.gov/investigations/AccidentReports/Reports/{}.pdf"
# The one link in the fixture page's reports feed.
FEED_LINK = '<li> <a href="/investigations/AccidentReports/Reports/MIR2691.pdf">MIR-26-91</a> </li>'
assert PAGE_HTML.count(FEED_LINK) == 1

# The in-scope investigations in the export, in its order: (id, page title, report number).
IN_SCOPE = [
    ("DCA25FM907", "Contact of Bulk Carrier Northwind Trader with Test Pier", "MIR2691"),
    ("DCA25FM906", "Collision of Saltmarsh Spirit with Tow of Lark Bay", "MAR2692"),
    ("DCA25FM910", "Capsizing of Crane Barge Deep Test", "MIR2697"),
    ("DCA25FM909", "Contact of Tow of River Test with Test Ave. Bridge", "MIR2696"),
]


def page_html(number: str, title: str, report: str | None) -> str:
    """The fixture page, retitled, with its one report link pointing at `report` (or none)."""
    html = PAGE_HTML.replace("DCA25FM907", number).replace(
        "Contact of Bulk Carrier Northwind Trader with Test Pier", title
    )
    assert FEED_LINK in html
    if report is None:
        return html.replace(FEED_LINK, "")
    return html.replace("MIR2691", report)


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    pauses: list[float] = []
    monkeypatch.setattr(ntsb, "_sleep", pauses.append)
    return pauses


@pytest.fixture(autouse=True)
def mock_site():
    # Every test, so none can reach www.ntsb.gov.
    # assert_all_mocked: any request without a matching route raises.
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


def mock_pages(router) -> dict[str, respx.Route]:
    return {
        number: router.get(PAGE.format(number)).mock(
            return_value=httpx.Response(200, text=page_html(number, title, report))
        )
        for number, title, report in IN_SCOPE
    }


def write_export(tmp_path: Path, results: list[dict]) -> Path:
    data = json.loads(EXPORT.read_text())
    data["results"] = results
    data["paging"]["totalCount"] = len(results)
    path = tmp_path / "carol.json"
    path.write_text(json.dumps(data))
    return path


def export_result(number: str) -> dict:
    results = json.loads(EXPORT.read_text())["results"]
    return copy.deepcopy(next(r for r in results if r["ntsbNumber"] == number))


def test_lists_in_scope_reports_with_their_pdf(mock_site, no_pause):
    routes = mock_pages(mock_site)

    with httpx.Client() as client:
        reports = list_reports(client, export_path=EXPORT)

    assert reports[0] == ReportMeta(
        slug="ntsb-dca25fm907",
        title="Contact of Bulk Carrier Northwind Trader with Test Pier",
        page_url="https://www.ntsb.gov/investigations/Pages/DCA25FM907.aspx",
        published="2026-11-03",
        occurred="2025-09-14",
        vessel_types=["Cargo, Dry Bulk"],
        report_type="Director-approved brief",
        pdf_url="https://www.ntsb.gov/investigations/AccidentReports/Reports/MIR2691.pdf",
        source="ntsb",
    )
    assert [(r.slug, r.title, r.page_url, r.pdf_url) for r in reports] == [
        (f"ntsb-{number.lower()}", title, PAGE.format(number), PDF.format(report))
        for number, title, report in IN_SCOPE
    ]
    assert [(r.published, r.occurred) for r in reports] == [
        ("2026-11-03", "2025-09-14"),
        ("2026-10-21", "2025-08-01"),
        ("2026-10-07", "2025-07-28"),
        ("2026-10-01", "2025-07-24"),
    ]
    # A type with a comma is one type; each type once; a free-text "... Other" is no vessel.
    assert [r.vessel_types for r in reports] == [
        ["Cargo, Dry Bulk"],
        ["Towing/Barge", "Cargo, Liquid Bulk"],
        ["Specialty/Other"],
        ["Towing/Barge"],
    ]
    assert [r.report_type for r in reports] == [
        "Director-approved brief", "Report", "Director-approved brief", "Director-approved brief"
    ]
    assert {r.source for r in reports} == {"ntsb"}
    # One page each, and only for reports in scope; a pause between pages.
    assert [route.call_count for route in routes.values()] == [1, 1, 1, 1]
    assert no_pause == [ntsb.PAUSE_SECONDS] * 3


def test_out_of_scope_reports_are_skipped_with_a_reason(mock_site):
    mock_pages(mock_site)
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        list_reports(client, export_path=EXPORT, on_skip=skipped.append)

    assert [(s.slug, s.title, s.page_url, s.reason, s.dropped) for s in skipped] == [
        ("ntsb-dca26fm908", "DCA26FM908", PAGE.format("DCA26FM908"),
         "ongoing: no report yet", False),
        ("ntsb-dca25fm905", "DCA25FM905", PAGE.format("DCA25FM905"), "fishing vessel", False),
        ("ntsb-dca25fm904", "DCA25FM904", PAGE.format("DCA25FM904"),
         "recreational vessel", False),
        ("ntsb-dca25fm903", "DCA25FM903", PAGE.format("DCA25FM903"), "naval vessel", False),
        # Newest published first: the study (2026) before the closeout memo (2025).
        ("ntsb-dca25sr901", "DCA25SR901", PAGE.format("DCA25SR901"),
         "safety study, not a single casualty", False),
        ("ntsb-dca25fm902", "DCA25FM902", PAGE.format("DCA25FM902"),
         "no report: Closeout memo", False),
    ]


def test_limit_stops_after_n(mock_site):
    routes = mock_pages(mock_site)
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        reports = list_reports(client, export_path=EXPORT, limit=2, on_skip=skipped.append)

    assert [r.slug for r in reports] == ["ntsb-dca25fm907", "ntsb-dca25fm906"]
    assert [route.call_count for route in routes.values()] == [1, 1, 0, 0]
    # Nothing after the second report is read, so nothing after it is reported as left out.
    assert [s.slug for s in skipped] == ["ntsb-dca26fm908"]


def test_a_failed_investigation_page_is_dropped_and_named(mock_site):
    routes = mock_pages(mock_site)
    routes["DCA25FM906"].mock(return_value=httpx.Response(404))
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        reports = list_reports(client, export_path=EXPORT, on_skip=skipped.append)

    # The reports before and after the failed page are kept.
    assert [r.slug for r in reports] == ["ntsb-dca25fm907", "ntsb-dca25fm910", "ntsb-dca25fm909"]
    failed = [s for s in skipped if s.dropped]
    assert [(s.slug, s.title, s.page_url, s.reason) for s in failed] == [
        (
            "ntsb-dca25fm906",
            "DCA25FM906",
            PAGE.format("DCA25FM906"),
            "investigation page could not be read: HTTP 404 for " + PAGE.format("DCA25FM906"),
        )
    ]


def test_three_failed_pages_in_a_row_raise(mock_site):
    routes = mock_pages(mock_site)
    for number in ("DCA25FM906", "DCA25FM910", "DCA25FM909"):
        routes[number].mock(return_value=httpx.Response(403))
    skipped: list[SkippedReport] = []

    with httpx.Client() as client, pytest.raises(ListingError) as raised:
        list_reports(client, export_path=EXPORT, on_skip=skipped.append)

    assert "3 NTSB investigation pages in a row" in str(raised.value)
    assert PAGE.format("DCA25FM909") in str(raised.value)
    assert "HTTP 403" in str(raised.value)
    assert [s.slug for s in skipped if s.dropped] == ["ntsb-dca25fm906", "ntsb-dca25fm910"]


def test_a_good_page_resets_the_failure_count(tmp_path, mock_site):
    numbers = ["DCA25FM941", "DCA25FM942", "DCA25FM943", "DCA25FM944", "DCA25FM945"]
    for number, status in zip(numbers, [404, 404, 200, 404, 404]):
        page = page_html(number, f"Casualty {number}", "MIR2691") if status == 200 else ""
        mock_site.get(PAGE.format(number)).mock(
            return_value=httpx.Response(status, text=page)
        )
    results = [export_result("DCA25FM907") | {"ntsbNumber": number} for number in numbers]
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        reports = list_reports(
            client, export_path=write_export(tmp_path, results), on_skip=skipped.append
        )

    assert [r.slug for r in reports] == ["ntsb-dca25fm943"]
    assert len(skipped) == 4


@pytest.mark.parametrize(
    "content",
    [None, "not json {", '{"paging": {}}', '{"results": {"ntsbNumber": "X"}}'],
    ids=["missing", "not-json", "no-results", "results-not-a-list"],
)
def test_a_bad_export_raises_listing_error_naming_the_path(tmp_path, content):
    path = tmp_path / "carol.json"
    if content is not None:
        path.write_text(content)

    with httpx.Client() as client, pytest.raises(ListingError, match=str(path)):
        list_reports(client, export_path=path)


def test_an_export_of_one_page_of_results_raises(tmp_path):
    data = json.loads(EXPORT.read_text())
    data["paging"]["totalCount"] = 781
    path = tmp_path / "carol.json"
    path.write_text(json.dumps(data))

    with httpx.Client() as client, pytest.raises(ListingError, match="10 of 781"):
        list_reports(client, export_path=path)


def test_a_bare_list_of_results_is_read(tmp_path, mock_site):
    mock_pages(mock_site)
    path = tmp_path / "carol.json"
    path.write_text(json.dumps([export_result("DCA25FM907")]))

    with httpx.Client() as client:
        reports = list_reports(client, export_path=path)

    assert [r.slug for r in reports] == ["ntsb-dca25fm907"]


def test_exclusion_needs_a_typed_vessel(tmp_path):
    bridge_only = export_result("DCA25FM909") | {"vehicles": " Test Ave. Bridge Other"}
    untyped = export_result("DCA25FM905") | {"ntsbNumber": "DCA25FM921", "vehicles": " Pacific"}
    empty = export_result("DCA25FM904") | {"ntsbNumber": "DCA25FM922", "vehicles": ""}
    mixed = export_result("DCA25FM903") | {
        "ntsbNumber": "DCA25FM923", "vehicles": " Morning Gull Fishing,  Blue Heron Yacht"
    }
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        reports = list_reports(
            client,
            export_path=write_export(tmp_path, [bridge_only, untyped, empty, mixed]),
            on_skip=skipped.append,
        )

    assert reports == []
    assert [(s.slug, s.reason, s.dropped) for s in skipped] == [
        ("ntsb-dca25fm909", "no vessel listed", False),
        ("ntsb-dca25fm921", "no vessel listed", False),
        ("ntsb-dca25fm922", "no vessel listed", False),
        ("ntsb-dca25fm923", "no merchant vessel: fishing vessel, recreational vessel", False),
    ]


def test_a_merchant_vessel_keeps_a_collision_with_a_naval_one(tmp_path, mock_site):
    mock_pages(mock_site)
    collision = export_result("DCA25FM907") | {
        "vehicles": " Test Cutter Combatant/Military,  Northwind Trader Cargo, Dry Bulk"
    }

    with httpx.Client() as client:
        reports = list_reports(client, export_path=write_export(tmp_path, [collision]))

    assert [r.vessel_types for r in reports] == [["Combatant/Military", "Cargo, Dry Bulk"]]


def test_unreadable_results_and_pages_are_dropped_with_a_warning(tmp_path, mock_site):
    no_id = export_result("DCA25FM907") | {"ntsbNumber": None}
    unknown_type = export_result("DCA25FM907") | {
        "ntsbNumber": "DCA25FM931", "reportType": "Interim"
    }
    no_date = export_result("DCA25FM907") | {"ntsbNumber": "DCA25FM932", "publishedDate": None}
    no_pdf = export_result("DCA25FM907") | {"ntsbNumber": "DCA25FM933"}
    no_title = export_result("DCA25FM907") | {"ntsbNumber": "DCA25FM934"}
    mock_site.get(PAGE.format("DCA25FM933")).mock(
        return_value=httpx.Response(200, text=page_html("DCA25FM933", "A Casualty", None))
    )
    mock_site.get(PAGE.format("DCA25FM934")).mock(
        return_value=httpx.Response(200, text="<html><body><p>Moved</p></body></html>")
    )
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        reports = list_reports(
            client,
            export_path=write_export(tmp_path, [no_id, unknown_type, no_date, no_pdf, no_title]),
            on_skip=skipped.append,
        )

    assert reports == []
    assert [(s.slug, s.title, s.reason, s.dropped) for s in skipped] == [
        # No published date sorts first, as an ongoing investigation does.
        ("ntsb-dca25fm932", "DCA25FM932", "no published date: None", True),
        # Named by its place in the export, not in the sorted order.
        ("", "export result 1", "export result has no investigation id", True),
        ("ntsb-dca25fm931", "DCA25FM931", "report type 'Interim'", True),
        ("ntsb-dca25fm933", "A Casualty", "investigation page links no report PDF", True),
        ("ntsb-dca25fm934", "DCA25FM934", "investigation page has no title", True),
    ]


def test_a_page_with_more_than_one_pdf_is_dropped(tmp_path, mock_site):
    two_pdfs = PAGE_HTML.replace(FEED_LINK, FEED_LINK + FEED_LINK.replace("MIR2691", "MIR2691A"))
    mock_site.get(PAGE.format("DCA25FM907")).mock(return_value=httpx.Response(200, text=two_pdfs))
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        reports = list_reports(
            client,
            export_path=write_export(tmp_path, [export_result("DCA25FM907")]),
            on_skip=skipped.append,
        )

    assert reports == []
    assert [(s.slug, s.reason, s.dropped) for s in skipped] == [
        ("ntsb-dca25fm907", "investigation page lists more than one PDF", True)
    ]


def several_pdfs_page(number: str, title: str, reports: list[str]) -> str:
    """The fixture page, retitled, whose reports feed links each of `reports`."""
    html = page_html(number, title, reports[0])
    first = FEED_LINK.replace("MIR2691", reports[0])
    return html.replace(
        first, "".join(FEED_LINK.replace("MIR2691", report) for report in reports)
    )


def list_one_page(tmp_path, mock_site, number: str, html: str):
    mock_site.get(PAGE.format(number)).mock(return_value=httpx.Response(200, text=html))
    record = export_result("DCA25FM907") | {"ntsbNumber": number.lower()}
    skipped: list[SkippedReport] = []
    with httpx.Client() as client:
        reports = list_reports(
            client, export_path=write_export(tmp_path, [record]), on_skip=skipped.append
        )
    return reports, skipped


DALI_TITLE = (
    "Contact of Containership Dali with Francis Scott Key Bridge and Subsequent Bridge Collapse"
)


def test_a_listed_choice_picks_the_accident_report_from_several_pdfs(tmp_path, mock_site):
    html = several_pdfs_page("DCA24MM031", DALI_TITLE, ["MIR2510", "MIR2540"])

    reports, skipped = list_one_page(tmp_path, mock_site, "DCA24MM031", html)

    assert skipped == []
    assert [(r.slug, r.title, r.pdf_url) for r in reports] == [
        ("ntsb-dca24mm031", DALI_TITLE, PDF.format("MIR2540"))
    ]
    assert reports[0].pdf_url.endswith("MIR2540.pdf")


def test_el_faro_picks_the_marine_accident_report(tmp_path, mock_site):
    title = "Sinking of the Freight Vessel El Faro"
    html = several_pdfs_page("DCA16MM001", title, ["MSR1702", "MAR1701", "SPC1801"])

    reports, skipped = list_one_page(tmp_path, mock_site, "DCA16MM001", html)

    assert skipped == []
    assert [(r.slug, r.title, r.pdf_url) for r in reports] == [
        ("ntsb-dca16mm001", title, PDF.format("MAR1701"))
    ]
    assert reports[0].pdf_url.endswith("MAR1701.pdf")


def test_a_listed_choice_missing_from_the_page_is_left_out_with_a_true_reason(
    tmp_path, mock_site
):
    html = several_pdfs_page("DCA24MM031", DALI_TITLE, ["MIR2510", "MIR2541"])

    reports, skipped = list_one_page(tmp_path, mock_site, "DCA24MM031", html)

    assert reports == []
    assert [(s.slug, s.title, s.reason, s.dropped) for s in skipped] == [
        (
            "ntsb-dca24mm031",
            DALI_TITLE,
            "investigation page lists more than one PDF, and not the chosen report MIR2540.pdf",
            True,
        )
    ]


def test_a_listed_choice_at_two_links_is_left_out_with_a_true_reason(tmp_path, mock_site):
    # The chosen file again at another path, in another case: two links, so neither is taken.
    chosen_link = FEED_LINK.replace("MIR2691", "MIR2540")
    again = '<li> <a href="/investigations/Reports/mir2540.PDF">MIR-25-40</a> </li>'
    html = several_pdfs_page("DCA24MM031", DALI_TITLE, ["MIR2510", "MIR2540"])
    assert html.count(chosen_link) == 1
    html = html.replace(chosen_link, chosen_link + again)

    reports, skipped = list_one_page(tmp_path, mock_site, "DCA24MM031", html)

    assert reports == []
    assert [s.reason for s in skipped] == [
        "investigation page lists the chosen report MIR2540.pdf at more than one link"
    ]


def test_a_listed_choice_with_one_pdf_takes_that_pdf(tmp_path, mock_site):
    # One PDF on the page is used as it is, even when it is not the chosen file.
    html = page_html("DCA24MM031", DALI_TITLE, "MIR2510")

    reports, skipped = list_one_page(tmp_path, mock_site, "DCA24MM031", html)

    assert skipped == []
    assert [r.pdf_url for r in reports] == [PDF.format("MIR2510")]


def test_a_repeated_investigation_is_listed_once(tmp_path, mock_site):
    routes = mock_pages(mock_site)
    path = write_export(tmp_path, [export_result("DCA25FM907"), export_result("DCA25FM907")])

    with httpx.Client() as client:
        reports = list_reports(client, export_path=path)

    assert [r.slug for r in reports] == ["ntsb-dca25fm907"]
    assert routes["DCA25FM907"].call_count == 1


@pytest.mark.parametrize(
    ("vehicles", "expected"),
    [
        (" Silver Test Cargo, Dry Bulk", [("Silver Test", "Cargo, Dry Bulk")]),
        (
            " Lark Bay Towing/Barge,  Saltmarsh Spirit Cargo, Liquid Bulk",
            [("Lark Bay", "Towing/Barge"), ("Saltmarsh Spirit", "Cargo, Liquid Bulk")],
        ),
        (" Test Barge Towing/barge", [("Test Barge", "Towing/Barge")]),
        (" Plain Test Cargo", [("Plain Test", "Cargo")]),
        (" Deep Test Specialty/Other", [("Deep Test", "Specialty/Other")]),
        (" Test Ave. Bridge Other", [("Test Ave. Bridge Other", None)]),
        (" 131 vessels and an unknown number of barges. Other", [
            ("131 vessels and an unknown number of barges. Other", None)
        ]),
        (" Pacific Test", [("Pacific Test", None)]),
        # A type must follow a space: "Supercargo" is a name, not a cargo ship.
        (" Supercargo", [("Supercargo", None)]),
        ("", []),
    ],
)
def test_parse_vehicles(vehicles, expected):
    assert parse_vehicles(vehicles) == expected


def test_read_investigation_page():
    title, pdfs = read_investigation_page(PAGE_HTML, PAGE.format("DCA25FM907"))

    assert title == "Contact of Bulk Carrier Northwind Trader with Test Pier"
    assert pdfs == [PDF.format("MIR2691")]
    # The same link twice is one PDF.
    doubled = PAGE_HTML.replace(FEED_LINK, FEED_LINK * 2)
    assert read_investigation_page(doubled, PAGE.format("DCA25FM907"))[1] == [PDF.format("MIR2691")]


# The map pop-up a real investigation page carries before its title (DCA25FM009, saved 26 Nov).
HIDDEN_MAP = """<div id="modal_map" style="display:none;">
	<h1>Accident Location</h1>
	<div id="map_full"></div>
</div>"""


def test_the_fixture_page_has_the_hidden_map_before_its_title():
    assert PAGE_HTML.index(HIDDEN_MAP) < PAGE_HTML.index(
        "<h1>Contact of Bulk Carrier Northwind Trader with Test Pier</h1>"
    )


@pytest.mark.parametrize(
    "hidden",
    [
        HIDDEN_MAP,
        # The real map block with its two inner elements swapped: the inner </div> closes the
        # inner div only, and the hidden div's own closing tag ends the skip.
        '<div id="modal_map" style="display:none;"><div id="map_full"></div>'
        "<h1>Accident Location</h1></div>",
        # Hidden by its id alone, or by an inline style alone, in any spacing or case.
        '<div id="modal_map"><h1>Accident Location</h1></div>',
        "<section style='color: red; DISPLAY : None'><h1>Accident Location</h1></section>",
        # The h1 itself hidden.
        '<h1 style="display:none">Accident Location</h1>',
        # Nested elements of the same tag, void tags and an unclosed <p> inside the hidden one.
        '<div style="display:none"><div><br><img src="x.png"><p>map<h1>Accident Location</h1>'
        "</div><hr></div>",
        # A hidden element left unclosed ends with its parent.
        '<div><span style="display:none"><h1>Accident Location</h1></div>',
        # A hidden void tag hides nothing after it.
        '<input type="hidden" style="display:none">',
        # The map pop-up hidden some way the parser does not see (a renamed id, a class, the
        # `hidden` attribute): its heading is never the title, in any case or spacing.
        '<div id="map_modal" class="hidden"><h1>Accident Location</h1></div>',
        '<div class="modal" hidden><h1>  ACCIDENT\n location </h1></div>',
        # A heading that reads "Accident Location" is passed over even when nothing hides it.
        "<h1>Accident Location</h1>",
        # A self-closed hidden element is open, as a browser reads it (the slash is ignored),
        # until its closing tag.
        '<div id="modal_map" style="display:none;"/><h1>Accident Location</h1></div>',
        '<div style="display:none"/><h1>Map</h1></div>',
    ],
)
def test_a_hidden_heading_is_not_the_title(hidden):
    html = f"<html><body>{hidden}<h1>Grounding of Real Title</h1></body></html>"

    assert read_investigation_page(html, PAGE.format("DCA25FM009"))[0] == "Grounding of Real Title"


def test_a_page_whose_only_heading_is_the_hidden_map_has_no_title():
    html = f"<html><body>{HIDDEN_MAP}<p>No title here.</p></body></html>"

    assert read_investigation_page(html, PAGE.format("DCA25FM009"))[0] is None


def test_a_page_whose_only_heading_reads_accident_location_has_no_title():
    html = '<html><body><div class="hidden"><h1>Accident Location</h1></div></body></html>'

    assert read_investigation_page(html, PAGE.format("DCA25FM009"))[0] is None


def test_a_self_closed_hidden_element_never_closed_hides_the_rest():
    # A browser reads <div .../> as an open div, so the heading after it is hidden too: no
    # title, which the listing skips by name, never a wrong title.
    html = '<html><body><div style="display:none"/><h1>Grounding of Real Title</h1></body></html>'

    assert read_investigation_page(html, PAGE.format("DCA25FM009"))[0] is None


# CAROL's "Download Data (JSON)" file: a bare list, PascalCase keys, vessels nested in Vehicles.
REAL_EXPORT = FIXTURES / "ntsb_carol_export.json"
# The in-scope investigations in it, in its order: (id, page title, report number).
REAL_IN_SCOPE = [
    ("DCA25MM910", "Collision of Test Barge 1 with Test Trawler 2", "MIR2691"),
    ("DCA25MM911", "Grounding of Test Bulker 3", "MAR2692"),
]


def mock_real_pages(router) -> dict[str, respx.Route]:
    return {
        number: router.get(PAGE.format(number)).mock(
            return_value=httpx.Response(200, text=page_html(number, title, report))
        )
        for number, title, report in REAL_IN_SCOPE
    }


def real_record(number: str) -> dict:
    records = json.loads(REAL_EXPORT.read_text())
    return copy.deepcopy(next(r for r in records if r["NTSBNumber"] == number))


def write_real_export(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "carol_investigations_data.json"
    path.write_text(json.dumps(records))
    return path


def with_vessel_types(record: dict, *types: str) -> dict:
    """`record` with one vehicle per type, each a JSON list as in the real download."""
    mkey = record["MKey"]
    record["Vehicles"] = [
        {
            "Mkey": mkey, "VehicleNumber": n, "Engines": [], "Findings": [], "Events": [],
            "VehicleIds": [
                {"Mkey": mkey, "VehicleNumber": n, "VesselName": f"Test Ship {n}",
                 "VesselType": vessel_type}
            ],
            "Injuries": [],
        }
        for n, vessel_type in enumerate(types, start=1)
    ]
    return record


def test_real_export_layout_is_read(mock_site, no_pause):
    routes = mock_real_pages(mock_site)

    with httpx.Client() as client:
        reports = list_reports(client, export_path=REAL_EXPORT)

    assert reports == [
        # Its Vehicles, and the VehicleIds inside, are Python-repr strings.
        ReportMeta(
            slug="ntsb-dca25mm911",
            title="Grounding of Test Bulker 3",
            page_url=PAGE.format("DCA25MM911"),
            published="2026-02-14",
            occurred="2025-05-06",
            vessel_types=["Cargo, Dry Bulk"],
            report_type="Report",
            pdf_url=PDF.format("MAR2692"),
            source="ntsb",
        ),
        ReportMeta(
            slug="ntsb-dca25mm910",
            title="Collision of Test Barge 1 with Test Trawler 2",
            page_url=PAGE.format("DCA25MM910"),
            published="2026-01-13",
            occurred="2025-04-05",
            vessel_types=["Towing/Barge", "Fishing"],
            report_type="Director-approved brief",
            pdf_url=PDF.format("MIR2691"),
            source="ntsb",
        ),
    ]
    assert [route.call_count for route in routes.values()] == [1, 1]
    assert no_pause == [ntsb.PAUSE_SECONDS]


def test_real_export_layout_is_listed_newest_first(mock_site):
    mock_real_pages(mock_site)
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        reports = list_reports(client, export_path=REAL_EXPORT, limit=1, on_skip=skipped.append)

    # The file runs oldest first; the newest published report in scope comes first. Before it:
    # those with no published date (by event date, newest first), then the newer malformed one.
    assert [r.slug for r in reports] == ["ntsb-dca25mm911"]
    assert [s.slug for s in skipped] == [
        "ntsb-dca24mm904", "ntsb-dca24mm903", "ntsb-dca24mm902", "ntsb-dca24mm901",
        "ntsb-dca25mm912",
    ]


def test_real_export_counts_follow_the_ordered_rules(mock_site):
    mock_real_pages(mock_site)
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        reports = list_reports(client, export_path=REAL_EXPORT, on_skip=skipped.append)

    left_out = [(s.slug, s.reason, s.dropped) for s in skipped]
    slug, reason, dropped = left_out.pop(4)
    assert (slug, dropped) == ("ntsb-dca25mm912", True)
    assert reason.startswith("vessel list cannot be read: ")
    assert left_out == [
        ("ntsb-dca24mm904", "no report: no report type", False),
        ("ntsb-dca24mm903", "no report: None", False),
        ("ntsb-dca24mm902", "no report: Closeout memo", False),
        ("ntsb-dca24mm901", "ongoing: no report yet", False),
        ("ntsb-dca25mm908", "no vessel listed", False),
        ("ntsb-dca25mm907", "recreational vessel", False),
        ("ntsb-dca25mm906", "fishing vessel", False),
        ("ntsb-dca24sr905", "safety study, not a single casualty", False),
    ]
    assert [r.slug for r in reports] == ["ntsb-dca25mm911", "ntsb-dca25mm910"]
    assert len(skipped) + len(reports) == len(json.loads(REAL_EXPORT.read_text()))


def test_malformed_vehicles_string_is_dropped_not_fatal(tmp_path, mock_site):
    mock_real_pages(mock_site)
    cut_off = real_record("DCA25MM912")
    # Code, not a literal: never run, only refused.
    code = real_record("DCA25MM912") | {
        "NTSBNumber": "DCA25MM921", "Vehicles": "__import__('os').getcwd()"
    }
    not_a_list = real_record("DCA25MM912") | {"NTSBNumber": "DCA25MM922", "Vehicles": "42"}
    bad_ids = real_record("DCA25MM910") | {"NTSBNumber": "DCA25MM923"}
    bad_ids["Vehicles"][0]["VehicleIds"] = "[{'VesselType': 'Towing/Barge'"
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        reports = list_reports(
            client,
            export_path=write_real_export(
                tmp_path, [cut_off, code, not_a_list, bad_ids, real_record("DCA25MM911")]
            ),
            on_skip=skipped.append,
        )

    # The good investigation after the bad ones is still listed.
    assert [r.slug for r in reports] == ["ntsb-dca25mm911"]
    assert [(s.slug, s.dropped) for s in skipped] == [
        ("ntsb-dca25mm912", True),
        ("ntsb-dca25mm921", True),
        ("ntsb-dca25mm922", True),
        ("ntsb-dca25mm923", True),
    ]
    assert all(s.reason.startswith("vessel list cannot be read: ") for s in skipped)


def test_vessel_type_match_ignores_case(tmp_path, mock_site):
    mixed_case = with_vessel_types(
        real_record("DCA25MM910"), "towing/barge", "Towing/Barge", "CARGO, GENERAL"
    )
    fishing = with_vessel_types(real_record("DCA25MM906"), "FISHING", "fishing")
    naval = with_vessel_types(real_record("DCA25MM907"), "combatant/military", "Patrol/small craft")
    mock_real_pages(mock_site)
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        reports = list_reports(
            client,
            export_path=write_real_export(tmp_path, [mixed_case, fishing, naval]),
            on_skip=skipped.append,
        )

    # Each type once, in its known spelling.
    assert [r.vessel_types for r in reports] == [["Towing/Barge", "Cargo, General"]]
    assert [(s.slug, s.reason, s.dropped) for s in skipped] == [
        ("ntsb-dca25mm907", "naval vessel", False),
        ("ntsb-dca25mm906", "fishing vessel", False),
    ]


@pytest.mark.parametrize("flag", [True, "True", "true"])
def test_a_study_flag_may_be_a_bool_or_a_string(tmp_path, mock_site, flag):
    study = real_record("DCA25MM911") | {"IsStudy": flag}
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        list_reports(
            client, export_path=write_real_export(tmp_path, [study]), on_skip=skipped.append
        )

    assert [s.reason for s in skipped] == ["safety study, not a single casualty"]


def test_report_without_a_page_title_is_dropped(tmp_path, mock_site):
    untitled = PAGE_HTML.replace(
        "<h1>Contact of Bulk Carrier Northwind Trader with Test Pier</h1>", ""
    ).replace("DCA25FM907", "DCA25MM911")
    mock_site.get(PAGE.format("DCA25MM911")).mock(return_value=httpx.Response(200, text=untitled))
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        reports = list_reports(
            client,
            export_path=write_real_export(tmp_path, [real_record("DCA25MM911")]),
            on_skip=skipped.append,
        )

    assert reports == []
    assert [(s.slug, s.title, s.reason, s.dropped) for s in skipped] == [
        ("ntsb-dca25mm911", "DCA25MM911", "investigation page has no title", True)
    ]


def test_a_vessel_typed_other_is_not_a_vessel(tmp_path, mock_site):
    # In the download, VesselType "Other" marks a bridge, a platform or a pipeline: like the
    # search results' free-text "... Other", it neither keeps nor excludes an investigation.
    mock_real_pages(mock_site)
    other_only = with_vessel_types(real_record("DCA25MM908"), "Other")
    other_and_tow = with_vessel_types(real_record("DCA25MM910"), "Other", "Towing/Barge")
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        reports = list_reports(
            client,
            export_path=write_real_export(tmp_path, [other_only, other_and_tow]),
            on_skip=skipped.append,
        )

    assert [(s.slug, s.reason) for s in skipped] == [("ntsb-dca25mm908", "no vessel listed")]
    assert [r.vessel_types for r in reports] == [["Towing/Barge"]]


def test_any_cargo_subtype_is_merchant(tmp_path, mock_site):
    mock_real_pages(mock_site)
    container = with_vessel_types(real_record("DCA25MM911"), "Cargo, Container")
    ro_ro_and_trawler = with_vessel_types(real_record("DCA25MM910"), "Fishing", "CARGO, Ro-Ro")
    skipped: list[SkippedReport] = []

    with httpx.Client() as client:
        reports = list_reports(
            client,
            export_path=write_real_export(tmp_path, [container, ro_ro_and_trawler]),
            on_skip=skipped.append,
        )

    assert skipped == []
    # A type CAROL may add later keeps its own spelling.
    assert [r.vessel_types for r in reports] == [["Cargo, Container"], ["Fishing", "CARGO, Ro-Ro"]]


def test_search_results_are_listed_newest_published_first(tmp_path, mock_site):
    routes = mock_pages(mock_site)
    results = json.loads(EXPORT.read_text())["results"]
    # Same published date: the later casualty first.
    same_day = export_result("DCA25FM910") | {
        "ntsbNumber": "DCA25FM951", "eventDateUtc": "7/29/2025 8:20:00 AM"
    }
    mock_site.get(PAGE.format("DCA25FM951")).mock(
        return_value=httpx.Response(200, text=page_html("DCA25FM951", "A Casualty", "MIR2698"))
    )

    with httpx.Client() as client:
        reports = list_reports(
            client, export_path=write_export(tmp_path, [same_day, *reversed(results)]), limit=4
        )

    assert [r.slug for r in reports] == [
        "ntsb-dca25fm907", "ntsb-dca25fm906", "ntsb-dca25fm951", "ntsb-dca25fm910"
    ]
    assert routes["DCA25FM909"].call_count == 0
