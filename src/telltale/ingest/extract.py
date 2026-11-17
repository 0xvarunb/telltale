"""Cut PDF text into small citable passages (a sentence or two each), grouped by page."""

import json
import logging
import re
from pathlib import Path

from pypdf import PdfReader

from telltale.ingest.ocr import PageReader
from telltale.text import split_sentences

logger = logging.getLogger(__name__)

# "fire-\nfighting" -> "firefighting": a hyphen at a line end before a lower-case letter.
HYPHEN_BREAK = re.compile(r"(\w)-[ \t]*\r?\n\s*(?=[a-z])")
# Fragments this short (stray page numbers, bullets) are not worth citing.
MIN_SENTENCE_CHARS = 3
# A page with less text than this (none, or just a page number) is treated as scanned.
SCANNED_PAGE_CHARS = 20


def pdf_pages(path: Path) -> list[str]:
    """The text of each page, in order; an image-only page gives ""."""
    return [page.extract_text() or "" for page in PdfReader(path).pages]


def read_pages(
    path: Path, ocr: PageReader | None = None, cache_dir: Path | None = None
) -> list[tuple[str, bool]]:
    """(text, read by OCR) for each page, in order.

    A page with no text layer is read by `ocr` when one is given. OCR results are kept in
    `cache_dir` (one JSON file per PDF), so a re-run does not read the same page again. An OCR
    failure leaves that page empty and is logged; it never stops the run.
    """
    pages = pdf_pages(path)
    if ocr is None or all(len(text.strip()) >= SCANNED_PAGE_CHARS for text in pages):
        return [(text, False) for text in pages]

    cache_file = cache_dir / f"{path.stem}.json" if cache_dir is not None else None
    cached: dict[str, str] = {}
    if cache_file is not None and cache_file.is_file():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))

    result: list[tuple[str, bool]] = []
    for index, text in enumerate(pages):
        if len(text.strip()) >= SCANNED_PAGE_CHARS:
            result.append((text, False))
            continue
        key = str(index)
        if key not in cached:
            try:
                cached[key] = ocr(path, index)
            except Exception as error:  # a damaged page or a model problem
                logger.warning("OCR failed on page %d of %s: %s", index + 1, path.name, error)
                result.append((text, False))
                continue
        ocr_text = cached[key]
        result.append((ocr_text, True) if ocr_text.strip() else (text, False))

    if cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cached), encoding="utf-8")
    return result


def _page_sentences(page_text: str) -> list[str]:
    text = HYPHEN_BREAK.sub(r"\1", page_text)
    # Line breaks inside a PDF page are layout, not sentence ends.
    return [s for s in split_sentences(" ".join(text.split())) if len(s) >= MIN_SENTENCE_CHARS]


def to_passages(page_texts: list[str], max_chars: int = 220) -> list[tuple[int, str]]:
    """(1-based page, passage) pairs. Whole sentences are joined until the next would pass
    `max_chars`; a single longer sentence stays whole. Pages without text give nothing."""
    passages: list[tuple[int, str]] = []
    for page, page_text in enumerate(page_texts, start=1):
        buffer = ""
        for sentence in _page_sentences(page_text):
            if buffer and len(buffer) + 1 + len(sentence) > max_chars:
                passages.append((page, buffer))
                buffer = sentence
            else:
                buffer = f"{buffer} {sentence}" if buffer else sentence
        if buffer:
            passages.append((page, buffer))
    return passages


def group_by_page(
    passages: list[tuple[int, str]], size: int = 12
) -> list[tuple[int, list[str]]]:
    """Consecutive passages from the same page, at most `size` to a group, in order."""
    groups: list[tuple[int, list[str]]] = []
    for page, passage in passages:
        if groups and groups[-1][0] == page and len(groups[-1][1]) < size:
            groups[-1][1].append(passage)
        else:
            groups.append((page, [passage]))
    return groups
