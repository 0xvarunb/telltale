"""SQLite store of reports and their page-grouped passages. Re-running an ingest is safe."""

import json
import sqlite3
from pathlib import Path

from telltale.models import Chunk, ReportMeta
from telltale.sources.maib import labelled_title

# Shown after the title of every passage read by OCR: a scan can be misread, so check the PDF.
OCR_NOTE = " (scanned page, text read by OCR)"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    page_url TEXT NOT NULL,
    published TEXT NOT NULL,
    occurred TEXT,
    vessel_types TEXT NOT NULL,
    report_type TEXT NOT NULL,
    pdf_url TEXT
);
-- AUTOINCREMENT: a deleted chunk's id is never reused, so a saved search index that still
-- names it fails to load instead of pointing at different text.
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_slug TEXT NOT NULL REFERENCES reports(slug),
    page INTEGER NOT NULL,
    passages TEXT NOT NULL,
    passage_count INTEGER NOT NULL,
    ocr INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS chunks_by_report ON chunks(report_slug);
"""

REPORT_COLUMNS = "slug, title, page_url, published, occurred, vessel_types, report_type, pdf_url"


class Store:
    """Reports and chunks in one SQLite file. Use as a context manager or call `close()`."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        columns = [row[1] for row in self._db.execute("PRAGMA table_info(chunks)")]
        if "ocr" not in columns:  # a store made before OCR was added
            with self._db:
                self._db.execute("ALTER TABLE chunks ADD COLUMN ocr INTEGER NOT NULL DEFAULT 0")

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def upsert_report(self, meta: ReportMeta) -> None:
        """Insert or update one report. A report that gains or loses a PDF loses its chunks,
        since they came from the other source (the PDF or the GOV.UK page)."""
        old = self._db.execute(
            "SELECT pdf_url FROM reports WHERE slug = ?", (meta.slug,)
        ).fetchone()
        with self._db:
            self._db.execute(
                f"""INSERT INTO reports ({REPORT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    title = excluded.title, page_url = excluded.page_url,
                    published = excluded.published, occurred = excluded.occurred,
                    vessel_types = excluded.vessel_types, report_type = excluded.report_type,
                    pdf_url = excluded.pdf_url""",
                (
                    meta.slug,
                    meta.title,
                    meta.page_url,
                    meta.published,
                    meta.occurred,
                    json.dumps(meta.vessel_types),
                    meta.report_type,
                    meta.pdf_url,
                ),
            )
            if old is not None and (old[0] is None) != (meta.pdf_url is None):
                self._db.execute("DELETE FROM chunks WHERE report_slug = ?", (meta.slug,))

    def replace_chunks(
        self, slug: str, groups: list[tuple[int, list[str]]], ocr_pages: frozenset[int] = frozenset()
    ) -> None:
        """Make `groups` the report's only chunks; pages in `ocr_pages` were read by OCR.

        For a report with no PDF the chunks are its GOV.UK page text. If nothing has changed,
        nothing is written, so the chunks keep their ids.
        """
        row = self._db.execute("SELECT pdf_url FROM reports WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            raise ValueError(f"no report {slug!r} in the store")

        rows = [(page, json.dumps(passages), int(page in ocr_pages)) for page, passages in groups]
        existing = self._db.execute(
            "SELECT page, passages, ocr FROM chunks WHERE report_slug = ? ORDER BY id", (slug,)
        ).fetchall()
        if existing == rows:
            return

        with self._db:
            self._db.execute("DELETE FROM chunks WHERE report_slug = ?", (slug,))
            self._db.executemany(
                "INSERT INTO chunks (report_slug, page, passages, passage_count, ocr)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (slug, page, text, len(passages), ocr)
                    for (page, text, ocr), (_, passages) in zip(rows, groups)
                ],
            )

    def chunks(self) -> list[Chunk]:
        """Every chunk, in id order (the same order on every call)."""
        rows = self._db.execute(
            """SELECT c.id, c.report_slug, r.title, r.report_type, r.pdf_url, r.page_url, c.page,
                c.passages, c.ocr
            FROM chunks c JOIN reports r ON r.slug = c.report_slug
            ORDER BY c.id"""
        ).fetchall()
        return [
            Chunk(
                id=id_,
                report_slug=slug,
                report_title=labelled_title(title, report_type) + (OCR_NOTE if ocr else ""),
                url=pdf_url or page_url,
                page=page,
                passages=json.loads(passages),
                ocr=bool(ocr),
                web_page=pdf_url is None,
            )
            for id_, slug, title, report_type, pdf_url, page_url, page, passages, ocr in rows
        ]

    def reports(self) -> list[ReportMeta]:
        """Every stored report, newest first."""
        return self._reports("1 = 1")

    def ocr_pages(self) -> int:
        """How many stored pages were read by OCR."""
        return self._db.execute(
            "SELECT COUNT(DISTINCT report_slug || ':' || page) FROM chunks WHERE ocr = 1"
        ).fetchone()[0]

    def report_types(self) -> dict[str, int]:
        """How many stored reports there are of each MAIB report type."""
        rows = self._db.execute(
            "SELECT report_type, COUNT(*) FROM reports GROUP BY report_type ORDER BY report_type"
        ).fetchall()
        return dict(rows)

    def _reports(self, where: str) -> list[ReportMeta]:
        rows = self._db.execute(
            f"SELECT {REPORT_COLUMNS} FROM reports r WHERE {where} ORDER BY published DESC, slug"
        ).fetchall()
        return [
            ReportMeta(
                slug=slug,
                title=title,
                page_url=page_url,
                published=published,
                occurred=occurred,
                vessel_types=json.loads(vessel_types),
                report_type=report_type,
                pdf_url=pdf_url,
            )
            for slug, title, page_url, published, occurred, vessel_types, report_type, pdf_url in rows
        ]

    def missing_pdf(self) -> list[ReportMeta]:
        """Reports with no PDF link and no text from their GOV.UK page, newest first."""
        return self._reports(
            "pdf_url IS NULL"
            " AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.report_slug = r.slug)"
        )

    def without_text(self) -> list[ReportMeta]:
        """Reports with a PDF link but no chunks (download failed, unreadable or image-only
        PDF), newest first."""
        return self._reports(
            "pdf_url IS NOT NULL"
            " AND NOT EXISTS (SELECT 1 FROM chunks c WHERE c.report_slug = r.slug)"
        )

    def counts(self) -> dict[str, int]:
        """Keys `reports`, `with_pdf`, `from_page` (no PDF, text from the GOV.UK page), `chunks`,
        `passages`."""
        reports, with_pdf = self._db.execute(
            "SELECT COUNT(*), COUNT(pdf_url) FROM reports"
        ).fetchone()
        from_page = self._db.execute(
            "SELECT COUNT(*) FROM reports r WHERE pdf_url IS NULL"
            " AND EXISTS (SELECT 1 FROM chunks c WHERE c.report_slug = r.slug)"
        ).fetchone()[0]
        chunks, passages = self._db.execute(
            "SELECT COUNT(*), COALESCE(SUM(passage_count), 0) FROM chunks"
        ).fetchone()
        return {
            "reports": reports,
            "with_pdf": with_pdf,
            "from_page": from_page,
            "chunks": chunks,
            "passages": passages,
        }
