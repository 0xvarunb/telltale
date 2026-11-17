from pathlib import Path

import httpx
import pytest
import respx

from telltale.ingest.download import DownloadError, download_pdf, pdf_path
from telltale.models import ReportMeta

PDF_URL = "https://assets.publishing.service.gov.uk/media/test/report-alpha.pdf"
PDF_BYTES = b"%PDF-1.7\n% synthetic test body\n%%EOF\n"


def report(pdf_url: str | None = PDF_URL) -> ReportMeta:
    return ReportMeta(
        slug="test-report-alpha",
        title="Test report alpha",
        page_url="https://www.gov.uk/maib-reports/test-report-alpha",
        published="2026-03-12T10:15:00.000+00:00",
        occurred=None,
        vessel_types=["merchant-vessel-100-gross-tons-or-over"],
        report_type="investigation-report",
        pdf_url=pdf_url,
    )


@pytest.fixture
def mock_api():
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as router:
        yield router


def test_download_skips_existing_file(mock_api, tmp_path: Path):
    route = mock_api.get(PDF_URL).mock(return_value=httpx.Response(200, content=PDF_BYTES))
    existing = pdf_path(report(), tmp_path)
    existing.write_bytes(PDF_BYTES)

    with httpx.Client() as client:
        path = download_pdf(client, report(), tmp_path)

    assert path == existing
    assert route.call_count == 0
    assert len(mock_api.calls) == 0


def test_download_none_without_pdf_url(mock_api, tmp_path: Path):
    with httpx.Client() as client:
        assert download_pdf(client, report(pdf_url=None), tmp_path) is None
    assert len(mock_api.calls) == 0


def test_download_writes_file(mock_api, tmp_path: Path):
    route = mock_api.get(PDF_URL).mock(return_value=httpx.Response(200, content=PDF_BYTES))
    dest = tmp_path / "pdf"
    # An empty file and a leftover partial file from an interrupted run are not complete.
    dest.mkdir()
    name = pdf_path(report(), dest).name
    (dest / name).write_bytes(b"")
    (dest / f"{name}.part").write_bytes(b"%PDF-1.7 half")

    with httpx.Client() as client:
        path = download_pdf(client, report(), dest)

    assert path == dest / name
    assert path.read_bytes() == PDF_BYTES
    assert route.call_count == 1
    assert "telltale" in route.calls.last.request.headers["User-Agent"].lower()
    assert sorted(p.name for p in dest.iterdir()) == [name]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, content=b"server error"),
        httpx.Response(200, content=b"<html>not a pdf</html>"),
    ],
)
def test_failed_download_leaves_no_file(mock_api, tmp_path: Path, response):
    mock_api.get(PDF_URL).mock(return_value=response)

    with httpx.Client() as client, pytest.raises((DownloadError, httpx.HTTPStatusError)):
        download_pdf(client, report(), tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_download_retries_server_error(mock_api, tmp_path: Path, retry_waits):
    route = mock_api.get(PDF_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.ReadTimeout("read timed out"),
            httpx.Response(200, content=PDF_BYTES),
        ]
    )

    with httpx.Client() as client:
        path = download_pdf(client, report(), tmp_path)

    assert path.read_bytes() == PDF_BYTES
    assert route.call_count == 3
    assert retry_waits == [1.0, 2.0]
    assert sorted(p.name for p in tmp_path.iterdir()) == [pdf_path(report(), tmp_path).name]


def test_download_not_found_not_retried(mock_api, tmp_path: Path, retry_waits):
    route = mock_api.get(PDF_URL).mock(return_value=httpx.Response(404))

    with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError):
        download_pdf(client, report(), tmp_path)

    assert route.call_count == 1
    assert retry_waits == []
    assert list(tmp_path.iterdir()) == []


def test_changed_pdf_url_downloads_again(mock_api, tmp_path: Path):
    # A report whose chosen PDF changes (for example a synopsis replaced by the full report)
    # must not keep the old file.
    new_url = "https://assets.publishing.service.gov.uk/media/test/full-report-alpha.pdf"
    pdf_path(report(), tmp_path).write_bytes(b"%PDF-1.7\nold synopsis\n%%EOF\n")
    route = mock_api.get(new_url).mock(return_value=httpx.Response(200, content=PDF_BYTES))

    with httpx.Client() as client:
        path = download_pdf(client, report(pdf_url=new_url), tmp_path)

    assert route.call_count == 1
    assert path.read_bytes() == PDF_BYTES
    assert path.name.startswith("test-report-alpha-")
    assert path != pdf_path(report(), tmp_path)
