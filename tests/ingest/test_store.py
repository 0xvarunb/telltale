from dataclasses import replace
from pathlib import Path

import pytest

from telltale.ingest.store import Store
from telltale.models import ReportMeta

PDF_URL = "https://assets.publishing.service.gov.uk/media/test/report-alpha.pdf"
PRELIMINARY = "completed-preliminary-examination"


def report(slug: str = "test-report-alpha", pdf_url: str | None = PDF_URL, **changes) -> ReportMeta:
    meta = ReportMeta(
        slug=slug,
        title=f"Test report {slug}",
        page_url=f"https://www.gov.uk/maib-reports/{slug}",
        published="2026-03-12T10:15:00.000+00:00",
        occurred="2025-06-01",
        vessel_types=["merchant-vessel-100-gross-tons-or-over"],
        report_type="investigation-report",
        pdf_url=pdf_url,
    )
    return replace(meta, **changes)


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "telltale.sqlite") as store:
        yield store


def test_upsert_twice_keeps_one_row(store: Store):
    store.upsert_report(report())
    store.upsert_report(report(title="Test report alpha, revised"))

    assert store.counts()["reports"] == 1
    store.replace_chunks("test-report-alpha", [(1, ["The line parted."])])
    assert store.chunks()[0].report_title == "Test report alpha, revised"


def test_replace_chunks_replaces(store: Store):
    store.upsert_report(report())
    store.replace_chunks("test-report-alpha", [(1, ["Old one.", "Old two."]), (2, ["Old three."])])
    store.upsert_report(report())
    store.replace_chunks("test-report-alpha", [(4, ["New one."])])

    chunks = store.chunks()
    assert store.counts() == {
        "reports": 1, "with_pdf": 1, "from_page": 0, "chunks": 1, "passages": 1
    }
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.report_slug == "test-report-alpha"
    assert chunk.report_title == "Test report test-report-alpha"
    assert chunk.page == 4
    assert chunk.passages == ["New one."]
    assert chunk.source == f"{PDF_URL}#page=4"


def test_missing_pdf_listed(store: Store):
    store.upsert_report(report())
    no_pdf = report("test-report-bravo", pdf_url=None)
    store.upsert_report(no_pdf)

    assert store.missing_pdf() == [no_pdf]
    assert store.counts() == {
        "reports": 2, "with_pdf": 1, "from_page": 0, "chunks": 0, "passages": 0
    }


def test_report_without_pdf_is_stored_from_its_page(store: Store):
    page_url = "https://www.gov.uk/maib-reports/test-report-bravo"
    store.upsert_report(report("test-report-bravo", pdf_url=None, report_type=PRELIMINARY))
    store.replace_chunks("test-report-bravo", [(1, ["The pilot over-corrected."])])

    [chunk] = store.chunks()
    assert chunk.web_page
    assert chunk.url == page_url
    # The page has no page numbers, so the source is the page itself.
    assert chunk.source == page_url
    assert chunk.report_title == "Preliminary examination: Test report test-report-bravo"
    assert store.missing_pdf() == []
    assert store.counts()["from_page"] == 1


def test_replace_chunks_needs_a_stored_report(store: Store):
    with pytest.raises(ValueError):
        store.replace_chunks("test-report-unknown", [(1, ["Text."])])


def test_losing_pdf_drops_chunks(store: Store):
    store.upsert_report(report())
    store.replace_chunks("test-report-alpha", [(1, ["The line parted."])])
    store.upsert_report(report(pdf_url=None))

    assert store.chunks() == []
    assert [m.slug for m in store.missing_pdf()] == ["test-report-alpha"]


def test_gaining_pdf_drops_page_text_chunks(store: Store):
    store.upsert_report(report(pdf_url=None))
    store.replace_chunks("test-report-alpha", [(1, ["From the page."])])
    store.upsert_report(report())

    # Page text must never be cited as a page of the new PDF.
    assert store.chunks() == []


def test_page_text_chunks_kept_while_still_no_pdf(store: Store):
    store.upsert_report(report(pdf_url=None))
    store.replace_chunks("test-report-alpha", [(1, ["From the page."])])
    first = store.chunks()
    store.upsert_report(report(pdf_url=None))
    store.replace_chunks("test-report-alpha", [(1, ["From the page."])])

    assert store.chunks() == first


def test_chunk_ids_stable_across_rerun_and_reopen(tmp_path: Path):
    path = tmp_path / "telltale.sqlite"
    groups_a = [(1, ["Alpha one.", "Alpha two."]), (2, ["Alpha three."])]
    groups_b = [(3, ["Bravo one."])]

    with Store(path) as store:
        store.upsert_report(report("test-report-alpha"))
        store.upsert_report(report("test-report-bravo"))
        store.replace_chunks("test-report-alpha", groups_a)
        store.replace_chunks("test-report-bravo", groups_b)
        first = store.chunks()

    # A second ingest run over the same reports and text changes nothing.
    with Store(path) as store:
        store.upsert_report(report("test-report-alpha"))
        store.upsert_report(report("test-report-bravo"))
        store.replace_chunks("test-report-alpha", groups_a)
        store.replace_chunks("test-report-bravo", groups_b)
        second = store.chunks()

    assert second == first
    assert [c.id for c in first] == sorted(c.id for c in first)
    assert [(c.report_slug, c.page) for c in first] == [
        ("test-report-alpha", 1),
        ("test-report-alpha", 2),
        ("test-report-bravo", 3),
    ]


def test_changed_chunks_get_new_ids(store: Store):
    store.upsert_report(report())
    store.replace_chunks("test-report-alpha", [(1, ["Old."])])
    old_id = store.chunks()[0].id
    store.replace_chunks("test-report-alpha", [(1, ["New."])])

    assert store.chunks()[0].id != old_id


def test_without_text_lists_pdf_reports_with_no_chunks(store: Store):
    store.upsert_report(report("test-report-alpha", published="2026-01-01"))
    store.upsert_report(report("test-report-bravo", published="2026-02-01"))
    store.upsert_report(report("test-report-charlie", published="2026-03-01"))
    store.upsert_report(report("test-report-delta", pdf_url=None))
    store.replace_chunks("test-report-alpha", [(1, ["The line parted."])])
    # An image-only PDF gives no groups.
    store.replace_chunks("test-report-charlie", [])

    assert [m.slug for m in store.without_text()] == ["test-report-charlie", "test-report-bravo"]
    # A report with no PDF link is listed by missing_pdf, not here.
    assert [m.slug for m in store.missing_pdf()] == ["test-report-delta"]


def test_chunk_titles_carry_the_report_type(store: Store):
    store.upsert_report(report("test-report-alpha"))
    store.upsert_report(report("test-report-bravo", report_type="safety-bulletin"))
    store.replace_chunks("test-report-alpha", [(1, ["The line parted."])])
    store.replace_chunks("test-report-bravo", [(1, ["Check the hooks."])])

    titles = [c.report_title for c in store.chunks()]

    assert titles == ["Test report test-report-alpha", "Safety bulletin: Test report test-report-bravo"]


def test_report_types_counted(store: Store):
    store.upsert_report(report("test-report-alpha"))
    store.upsert_report(report("test-report-bravo", report_type="safety-bulletin"))
    store.upsert_report(report("test-report-charlie", report_type="safety-bulletin"))

    assert store.report_types() == {"investigation-report": 1, "safety-bulletin": 2}


def test_ocr_pages_are_marked_and_counted(store: Store):
    store.upsert_report(report())
    store.replace_chunks(
        "test-report-alpha", [(1, ["Typed text."]), (2, ["Scanned text."])], frozenset({2})
    )

    chunks = store.chunks()

    assert [c.ocr for c in chunks] == [False, True]
    assert chunks[0].report_title == "Test report test-report-alpha"
    assert chunks[1].report_title == "Test report test-report-alpha (scanned page, text read by OCR)"
    assert store.ocr_pages() == 1


def test_changed_ocr_flag_is_written(store: Store):
    store.upsert_report(report())
    store.replace_chunks("test-report-alpha", [(1, ["Same text."])])
    store.replace_chunks("test-report-alpha", [(1, ["Same text."])], frozenset({1}))

    assert store.chunks()[0].ocr is True


def test_store_made_before_ocr_is_upgraded(tmp_path: Path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        """CREATE TABLE reports (slug TEXT PRIMARY KEY, title TEXT NOT NULL,
        page_url TEXT NOT NULL, published TEXT NOT NULL, occurred TEXT,
        vessel_types TEXT NOT NULL, report_type TEXT NOT NULL, pdf_url TEXT);
        CREATE TABLE chunks (id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_slug TEXT NOT NULL REFERENCES reports(slug), page INTEGER NOT NULL,
        passages TEXT NOT NULL, passage_count INTEGER NOT NULL);"""
    )
    db.close()

    with Store(path) as store:
        store.upsert_report(report())
        store.replace_chunks("test-report-alpha", [(1, ["Old store, new column."])])
        assert store.chunks()[0].ocr is False
