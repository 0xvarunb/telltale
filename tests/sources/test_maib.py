import copy
import json
from pathlib import Path

import httpx
import pytest
import respx

from telltale.sources import maib
from telltale.sources.maib import list_merchant_reports, pick_pdf_url

FIXTURES = Path(__file__).parent.parent / "fixtures"
SEARCH_URL = "https://www.gov.uk/api/search.json"
CONTENT_URL = "https://www.gov.uk/api/content"
SEARCH_FILTERS = {
    "filter_format": "maib_report",
    "filter_vessel_type": "merchant-vessel-100-gross-tons-or-over",
    "fields": "title,link,public_timestamp",
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    pauses: list[float] = []
    monkeypatch.setattr(maib, "_sleep", pauses.append)
    return pauses


@pytest.fixture
def mock_api():
    # assert_all_mocked: any request without a matching route raises.
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


def mock_search(router, page: dict, count: int, start: int):
    return router.get(
        SEARCH_URL, params={**SEARCH_FILTERS, "count": str(count), "start": str(start)}
    ).mock(return_value=httpx.Response(200, json=page))


def two_search_pages() -> tuple[dict, dict]:
    full = load("govuk_search_page.json")
    first = {**full, "results": full["results"][:2], "start": 0}
    second = {**full, "results": full["results"][2:], "start": 2}
    return first, second


def mock_content(router, slug: str, body: dict):
    return router.get(f"{CONTENT_URL}/maib-reports/{slug}").mock(
        return_value=httpx.Response(200, json=body)
    )


def test_pages_through_results(mock_api, no_pause):
    first, second = two_search_pages()
    page_one = mock_search(mock_api, first, count=2, start=0)
    page_two = mock_search(mock_api, second, count=2, start=2)
    content_routes = [
        mock_content(mock_api, "test-vessel-report-alpha", load("govuk_content_attachments.json")),
        mock_content(mock_api, "test-vessel-report-bravo", load("govuk_content_body_link.json")),
        mock_content(mock_api, "test-vessel-report-charlie", load("govuk_content_no_pdf.json")),
    ]

    with httpx.Client() as client:
        reports = list_merchant_reports(client, page_size=2)

    assert page_one.call_count == 1
    assert page_two.call_count == 1
    assert sum(route.call_count for route in content_routes) == 3
    assert [r.slug for r in reports] == [
        "test-vessel-report-alpha",
        "test-vessel-report-bravo",
        "test-vessel-report-charlie",
    ]

    alpha = reports[0]
    assert alpha.title == "Test vessel report alpha"
    assert alpha.page_url == "https://www.gov.uk/maib-reports/test-vessel-report-alpha"
    assert alpha.published == "2026-03-12T10:15:00.000+00:00"
    assert alpha.occurred == "2025-06-01"
    assert alpha.vessel_types == ["merchant-vessel-100-gross-tons-or-over"]
    assert alpha.report_type == "investigation-report"
    assert alpha.pdf_url == "https://assets.publishing.service.gov.uk/media/bbb222/full-report.pdf"
    assert reports[1].pdf_url == "https://assets.publishing.service.gov.uk/media/abc/report.pdf"

    # A pause between content calls (none before the first), sent with a named User-Agent.
    assert no_pause == [0.2, 0.2]
    for route in [page_one, page_two, *content_routes]:
        assert "telltale" in route.calls.last.request.headers["User-Agent"].lower()


def test_limit_stops_paging(mock_api):
    first, _ = two_search_pages()
    page_one = mock_search(mock_api, first, count=2, start=0)
    mock_content(mock_api, "test-vessel-report-alpha", load("govuk_content_attachments.json"))

    with httpx.Client() as client:
        reports = list_merchant_reports(client, page_size=2, limit=1)

    assert [r.slug for r in reports] == ["test-vessel-report-alpha"]
    assert page_one.call_count == 1
    # Only one search call and one content call were made; nothing else was requested.
    assert len(mock_api.calls) == 2


def test_keeps_investigation_reports_only(mock_api):
    # Ruling 23: full investigations, Red Ensign Group investigations, safety bulletins and
    # preliminary examinations are kept; other report types (here an overseas report) are not.
    first, second = two_search_pages()
    mock_search(mock_api, first, count=2, start=0)
    mock_search(mock_api, second, count=2, start=2)
    overseas = copy.deepcopy(load("govuk_content_attachments.json"))
    overseas["details"]["metadata"]["report_type"] = "overseas-report"
    mock_content(mock_api, "test-vessel-report-alpha", overseas)
    mock_content(mock_api, "test-vessel-report-bravo", load("govuk_content_body_link.json"))
    mock_content(mock_api, "test-vessel-report-charlie", load("govuk_content_no_pdf.json"))

    with httpx.Client() as client:
        reports = list_merchant_reports(client, page_size=2)

    assert [r.slug for r in reports] == [
        "test-vessel-report-bravo",
        "test-vessel-report-charlie",
    ]
    assert all(r.report_type == "investigation-report" for r in reports)


@pytest.mark.parametrize(
    "report_type",
    [
        "safety-bulletin",
        "completed-preliminary-examination",
        "investigation-report-on-behalf-of-red-ensign-group",
    ],
)
def test_keeps_bulletins_preliminary_examinations_and_red_ensign_reports(mock_api, report_type):
    first, second = two_search_pages()
    mock_search(mock_api, first, count=2, start=0)
    mock_search(mock_api, second, count=2, start=2)
    kept = copy.deepcopy(load("govuk_content_attachments.json"))
    kept["details"]["metadata"]["report_type"] = report_type
    mock_content(mock_api, "test-vessel-report-alpha", kept)
    mock_content(mock_api, "test-vessel-report-bravo", load("govuk_content_body_link.json"))
    mock_content(mock_api, "test-vessel-report-charlie", load("govuk_content_no_pdf.json"))

    with httpx.Client() as client:
        reports = list_merchant_reports(client, page_size=2)

    assert reports[0].slug == "test-vessel-report-alpha"
    assert reports[0].report_type == report_type


def test_labelled_title_marks_everything_but_full_investigations():
    assert maib.labelled_title("Grounding of Test Ship", "investigation-report") == (
        "Grounding of Test Ship"
    )
    assert maib.labelled_title("Lifeboat hooks", "safety-bulletin") == (
        "Safety bulletin: Lifeboat hooks"
    )
    assert maib.labelled_title("Fall into hold", "completed-preliminary-examination") == (
        "Preliminary examination: Fall into hold"
    )
    red_ensign = "investigation-report-on-behalf-of-red-ensign-group"
    assert maib.labelled_title("Fire on Test Ship", red_ensign) == (
        "Red Ensign Group investigation: Fire on Test Ship"
    )


def test_pick_pdf_prefers_largest_attachment():
    content = load("govuk_content_attachments.json")

    assert (
        pick_pdf_url(content)
        == "https://assets.publishing.service.gov.uk/media/bbb222/full-report.pdf"
    )


def test_pick_pdf_skips_synopsis_when_sizes_are_missing():
    # Older GOV.UK pages list a synopsis first and give no file sizes (seen 4 Nov 2026).
    media = "https://assets.digital.cabinet-office.gov.uk/media"
    content = {
        "details": {
            "attachments": [
                {"content_type": "application/pdf", "file_size": None, "title": "Synopsis",
                 "url": f"{media}/a1/TestShipSynopsis.pdf"},
                {"content_type": "application/pdf", "file_size": None, "title": "TestShipReport.pdf",
                 "url": f"{media}/a2/TestShipReport.pdf"},
                {"content_type": "application/pdf", "file_size": None, "title": "Annexes Part 1",
                 "url": f"{media}/a3/TestShipAnnexesA-D.pdf"},
            ]
        }
    }

    assert pick_pdf_url(content) == f"{media}/a2/TestShipReport.pdf"


def test_pick_pdf_report_without_report_in_its_name():
    media = "https://assets.digital.cabinet-office.gov.uk/media"
    content = {
        "details": {
            "attachments": [
                {"content_type": "application/pdf", "title": "Synopsis",
                 "url": f"{media}/b1/test-ship_synopsis.pdf"},
                {"content_type": "application/pdf", "title": "test-ship.pdf",
                 "url": f"{media}/b2/test-ship.pdf"},
            ]
        }
    }

    assert pick_pdf_url(content) == f"{media}/b2/test-ship.pdf"


def test_pick_pdf_keeps_a_lone_synopsis():
    url = "https://assets.digital.cabinet-office.gov.uk/media/c1/test-ship_synopsis.pdf"
    content = {"details": {"attachments": [
        {"content_type": "application/pdf", "title": "Synopsis", "url": url},
    ]}}

    assert pick_pdf_url(content) == url


def test_pick_pdf_body_link_on_older_asset_host():
    url = "https://assets.digital.cabinet-office.gov.uk/media/d1/TestShipReport.pdf"
    content = {"details": {"body": f'<p>Read the <a href="{url}">report</a>.</p>'}}

    assert pick_pdf_url(content) == url


def test_pick_pdf_falls_back_to_body_link():
    content = load("govuk_content_body_link.json")

    assert pick_pdf_url(content) == "https://assets.publishing.service.gov.uk/media/abc/report.pdf"


def test_pick_pdf_none_when_missing(mock_api):
    content = load("govuk_content_no_pdf.json")
    assert pick_pdf_url(content) is None

    page = {**load("govuk_search_page.json")}
    page["results"] = page["results"][2:]
    page["total"] = 1
    mock_search(mock_api, page, count=100, start=0)
    mock_content(mock_api, "test-vessel-report-charlie", content)

    with httpx.Client() as client:
        reports = list_merchant_reports(client)

    assert len(reports) == 1
    charlie = reports[0]
    assert charlie.slug == "test-vessel-report-charlie"
    assert charlie.pdf_url is None
    # With no PDF, the page body is the report text.
    assert charlie.page_html == "<p>Synthetic summary with no report file.</p>"
    assert charlie.occurred is None
    assert charlie.vessel_types == ["merchant-vessel-100-gross-tons-or-over", "fishing-vessel"]


def one_result_page(total: int | None = 1) -> dict:
    page = load("govuk_search_page.json")
    page["results"] = page["results"][:1]
    if total is None:
        del page["total"]
    else:
        page["total"] = total
    return page


def test_retries_429_honouring_retry_after(mock_api, retry_waits):
    route = mock_api.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}),
            httpx.Response(200, json=one_result_page()),
        ]
    )
    mock_content(mock_api, "test-vessel-report-alpha", load("govuk_content_attachments.json"))

    with httpx.Client() as client:
        reports = list_merchant_reports(client)

    assert [r.slug for r in reports] == ["test-vessel-report-alpha"]
    assert route.call_count == 2
    assert retry_waits == [3.0]


def test_retry_after_is_capped(mock_api, retry_waits):
    mock_api.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "86400"}),
            httpx.Response(200, json=one_result_page()),
        ]
    )
    mock_content(mock_api, "test-vessel-report-alpha", load("govuk_content_attachments.json"))

    with httpx.Client() as client:
        list_merchant_reports(client)

    assert retry_waits == [60.0]


def test_transport_errors_retried_with_backoff_then_succeed(mock_api, retry_waits):
    route = mock_api.get(SEARCH_URL).mock(
        side_effect=[
            httpx.ConnectTimeout("connect timed out"),
            httpx.ReadTimeout("read timed out"),
            httpx.Response(502),
            httpx.Response(200, json=one_result_page()),
        ]
    )
    mock_content(mock_api, "test-vessel-report-alpha", load("govuk_content_attachments.json"))

    with httpx.Client() as client:
        reports = list_merchant_reports(client)

    assert len(reports) == 1
    assert route.call_count == 4
    assert retry_waits == [1.0, 2.0, 4.0]


def test_gives_up_after_six_tries(mock_api, retry_waits):
    route = mock_api.get(SEARCH_URL).mock(return_value=httpx.Response(500))

    with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError):
        list_merchant_reports(client)

    assert route.call_count == 6
    assert retry_waits == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_client_error_not_retried(mock_api, retry_waits):
    route = mock_api.get(SEARCH_URL).mock(return_value=httpx.Response(404))

    with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError):
        list_merchant_reports(client)

    assert route.call_count == 1
    assert retry_waits == []


def test_follows_redirects(mock_api):
    mock_search(mock_api, one_result_page(), count=100, start=0)
    mock_api.get(f"{CONTENT_URL}/maib-reports/test-vessel-report-alpha").mock(
        return_value=httpx.Response(
            301, headers={"Location": f"{CONTENT_URL}/maib-reports/test-vessel-report-alpha-moved"}
        )
    )
    mock_content(
        mock_api, "test-vessel-report-alpha-moved", load("govuk_content_attachments.json")
    )

    # A client built without follow_redirects still follows them.
    with httpx.Client(follow_redirects=False) as client:
        reports = list_merchant_reports(client)

    assert [r.slug for r in reports] == ["test-vessel-report-alpha"]


def test_missing_total_raises(mock_api):
    mock_search(mock_api, one_result_page(total=None), count=100, start=0)

    with httpx.Client() as client, pytest.raises(maib.ListingError, match="no total"):
        list_merchant_reports(client)


def test_skipped_reports_counted_and_logged(mock_api, caplog):
    first, second = two_search_pages()
    mock_search(mock_api, first, count=2, start=0)
    mock_search(mock_api, second, count=2, start=2)
    bulletin = copy.deepcopy(load("govuk_content_attachments.json"))
    bulletin["details"]["metadata"]["report_type"] = "overseas-report"
    untyped = copy.deepcopy(load("govuk_content_body_link.json"))
    del untyped["details"]["metadata"]["report_type"]
    mock_content(mock_api, "test-vessel-report-alpha", bulletin)
    mock_content(mock_api, "test-vessel-report-bravo", untyped)
    mock_api.get(f"{CONTENT_URL}/maib-reports/test-vessel-report-charlie").mock(
        return_value=httpx.Response(410)
    )
    skipped: list[maib.SkippedReport] = []

    with caplog.at_level("WARNING", logger="telltale.sources.maib"), httpx.Client() as client:
        reports = list_merchant_reports(client, page_size=2, on_skip=skipped.append)

    assert reports == []
    assert [(s.slug, s.dropped) for s in skipped] == [
        ("test-vessel-report-alpha", False),
        ("test-vessel-report-bravo", True),
        ("test-vessel-report-charlie", True),
    ]
    assert skipped[0].reason == "overseas-report"
    assert skipped[1].reason == "no report type"
    assert skipped[1].page_url == "https://www.gov.uk/maib-reports/test-vessel-report-bravo"
    assert "HTTP 410" in skipped[2].reason
    # The two dropped reports are warned about; the bulletin is an expected filter.
    warned = " ".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")
    assert "test-vessel-report-bravo" in warned
    assert "test-vessel-report-charlie" in warned
    assert "test-vessel-report-alpha" not in warned


def test_slug_repeated_on_next_page_fetched_once(mock_api):
    first, second = two_search_pages()
    second = {**second, "results": [first["results"][1], *second["results"]], "total": 4}
    mock_search(mock_api, first, count=2, start=0)
    mock_search(mock_api, second, count=2, start=2)
    bravo = mock_content(
        mock_api, "test-vessel-report-bravo", load("govuk_content_body_link.json")
    )
    mock_content(mock_api, "test-vessel-report-alpha", load("govuk_content_attachments.json"))
    mock_content(mock_api, "test-vessel-report-charlie", load("govuk_content_no_pdf.json"))

    with httpx.Client() as client:
        reports = list_merchant_reports(client, page_size=2)

    assert [r.slug for r in reports] == [
        "test-vessel-report-alpha",
        "test-vessel-report-bravo",
        "test-vessel-report-charlie",
    ]
    assert bravo.call_count == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>maintenance page</html>"),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
)
def test_content_that_is_not_a_json_object_is_dropped(mock_api, response):
    first, second = two_search_pages()
    mock_search(mock_api, first, count=2, start=0)
    mock_search(mock_api, second, count=2, start=2)
    mock_api.get(f"{CONTENT_URL}/maib-reports/test-vessel-report-alpha").mock(
        return_value=response
    )
    mock_content(mock_api, "test-vessel-report-bravo", load("govuk_content_body_link.json"))
    mock_content(mock_api, "test-vessel-report-charlie", load("govuk_content_no_pdf.json"))
    skipped: list[maib.SkippedReport] = []

    with httpx.Client() as client:
        reports = list_merchant_reports(client, page_size=2, on_skip=skipped.append)

    # The listing carries on past the bad report, which is recorded with its page link.
    assert [r.slug for r in reports] == ["test-vessel-report-bravo", "test-vessel-report-charlie"]
    assert len(skipped) == 1
    assert skipped[0].dropped
    assert skipped[0].page_url == "https://www.gov.uk/maib-reports/test-vessel-report-alpha"
    assert skipped[0].reason.startswith("content request failed")


def _break(path: tuple[str, ...], value):
    def change(content: dict) -> None:
        target = content
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return change


@pytest.mark.parametrize(
    "change",
    [
        _break(("details",), ["not", "a", "dict"]),
        _break(("details", "metadata"), "not a dict"),
        _break(("details", "attachments"), {"url": "not a list"}),
        _break(("details", "attachments"), ["not a dict"]),
        _break(("details", "body"), 42),
        _break(("details", "metadata", "report_type"), {"not": "text"}),
    ],
    ids=["details", "metadata", "attachments-dict", "attachment-str", "body", "report-type"],
)
def test_malformed_content_body_is_dropped(mock_api, change, caplog):
    first, second = two_search_pages()
    mock_search(mock_api, first, count=2, start=0)
    mock_search(mock_api, second, count=2, start=2)
    broken = copy.deepcopy(load("govuk_content_body_link.json"))
    broken["details"].setdefault("attachments", [])
    change(broken)
    mock_content(mock_api, "test-vessel-report-alpha", broken)
    mock_content(mock_api, "test-vessel-report-bravo", load("govuk_content_body_link.json"))
    mock_content(mock_api, "test-vessel-report-charlie", load("govuk_content_no_pdf.json"))
    skipped: list[maib.SkippedReport] = []

    with caplog.at_level("WARNING", logger="telltale.sources.maib"), httpx.Client() as client:
        reports = list_merchant_reports(client, page_size=2, on_skip=skipped.append)

    assert [r.slug for r in reports] == ["test-vessel-report-bravo", "test-vessel-report-charlie"]
    assert len(skipped) == 1
    assert skipped[0].dropped
    assert skipped[0].slug == "test-vessel-report-alpha"
    assert skipped[0].title == "Test vessel report alpha"
    assert skipped[0].page_url == "https://www.gov.uk/maib-reports/test-vessel-report-alpha"
    assert skipped[0].reason.startswith("content could not be read")
    assert "test-vessel-report-alpha" in caplog.text


@pytest.mark.parametrize("missing", ["title", "public_timestamp"])
def test_search_result_missing_a_field_is_dropped_with_page_link(mock_api, missing):
    page = load("govuk_search_page.json")
    del page["results"][0][missing]
    mock_search(mock_api, page, count=100, start=0)
    mock_content(mock_api, "test-vessel-report-alpha", load("govuk_content_attachments.json"))
    mock_content(mock_api, "test-vessel-report-bravo", load("govuk_content_body_link.json"))
    mock_content(mock_api, "test-vessel-report-charlie", load("govuk_content_no_pdf.json"))
    skipped: list[maib.SkippedReport] = []

    with httpx.Client() as client:
        reports = list_merchant_reports(client, on_skip=skipped.append)

    assert [r.slug for r in reports] == ["test-vessel-report-bravo", "test-vessel-report-charlie"]
    assert [(s.slug, s.dropped) for s in skipped] == [("test-vessel-report-alpha", True)]
    assert skipped[0].page_url == "https://www.gov.uk/maib-reports/test-vessel-report-alpha"
    assert skipped[0].reason == f"content could not be read: KeyError: '{missing}'"


def test_search_result_without_link_is_dropped_and_warned(mock_api, caplog):
    page = load("govuk_search_page.json")
    del page["results"][0]["link"]
    page["results"][1] = {"link": None}
    page["results"].insert(0, "not a result")
    page["total"] = 4
    mock_search(mock_api, page, count=100, start=0)
    charlie = mock_content(
        mock_api, "test-vessel-report-charlie", load("govuk_content_no_pdf.json")
    )
    skipped: list[maib.SkippedReport] = []

    with caplog.at_level("WARNING", logger="telltale.sources.maib"), httpx.Client() as client:
        reports = list_merchant_reports(client, on_skip=skipped.append)

    # No content call is made without a link; the rest of the listing carries on.
    assert [r.slug for r in reports] == ["test-vessel-report-charlie"]
    assert charlie.call_count == 1
    assert [(s.title, s.page_url, s.dropped) for s in skipped] == [
        ("search result 1", "", True),
        ("Test vessel report alpha", "", True),
        ("search result 3", "", True),
    ]
    assert all(s.reason == "search result has no link" for s in skipped)
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 3
    assert "Test vessel report alpha" in warnings[1]
    assert "no page link" in warnings[1]


def test_page_html_only_kept_when_there_is_no_pdf(mock_api):
    mock_search(mock_api, load("govuk_search_page.json"), count=100, start=0)
    mock_content(mock_api, "test-vessel-report-alpha", load("govuk_content_attachments.json"))
    mock_content(mock_api, "test-vessel-report-bravo", load("govuk_content_body_link.json"))
    mock_content(mock_api, "test-vessel-report-charlie", load("govuk_content_no_pdf.json"))

    with httpx.Client() as client:
        reports = {r.slug: r for r in list_merchant_reports(client)}

    assert reports["test-vessel-report-alpha"].page_html is None
    assert reports["test-vessel-report-bravo"].page_html is None
    assert reports["test-vessel-report-charlie"].page_html is not None
