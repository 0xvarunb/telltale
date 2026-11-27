"""Download each report PDF once; a half-finished download never looks complete."""

import hashlib
import os
import re
from pathlib import Path

import httpx

from telltale.http import USER_AGENT, with_retry
from telltale.models import ReportMeta

PDF_MAGIC = b"%PDF"


class DownloadError(Exception):
    """The server answered, but not with a PDF."""


def pdf_path(meta: ReportMeta, dest: Path) -> Path:
    """Where the report's PDF lives inside `dest`.

    The name carries a short hash of the PDF's address, so a report whose chosen PDF changes
    is downloaded afresh instead of keeping the old file.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", meta.slug).lstrip(".")
    url_hash = hashlib.sha1((meta.pdf_url or "").encode()).hexdigest()[:10]
    return dest / f"{slug}-{url_hash}.pdf"


def already_downloaded(meta: ReportMeta, dest: Path) -> bool:
    """True when the report's PDF is already complete in `dest`, so `download_pdf` asks nothing."""
    path = pdf_path(meta, dest)
    return path.is_file() and path.stat().st_size > 0


def download_pdf(client: httpx.Client, meta: ReportMeta, dest: Path) -> Path | None:
    """The report's PDF in directory `dest`, downloaded only if not already there.

    `None` when the report has no PDF. Bytes go to a `.part` file that is renamed only once
    the whole body has arrived and starts like a PDF, so an interrupted run leaves no file
    that a later run would skip. A 429, a 5xx or a dropped connection is retried a few times.
    """
    if meta.pdf_url is None:
        return None

    path = pdf_path(meta, dest)
    if already_downloaded(meta, dest):
        return path

    dest.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")

    def fetch() -> None:
        # Each try starts the .part file afresh.
        with client.stream(
            "GET", meta.pdf_url, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as response:
            response.raise_for_status()
            with part.open("wb") as f:
                for block in response.iter_bytes():
                    f.write(block)

    try:
        with_retry(fetch)
        with part.open("rb") as f:
            if PDF_MAGIC not in f.read(1024):
                raise DownloadError(f"{meta.pdf_url} did not return a PDF")
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    return path
