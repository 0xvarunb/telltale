from pathlib import Path

import pytest
from aes_pdf import write_aes_encrypted_pdf
from pypdf import PdfReader, PdfWriter
from pypdf.errors import DependencyError

from telltale.ingest.extract import group_by_page, pdf_pages, read_pages, to_passages


def sentence(n: int, length: int = 100) -> str:
    """A synthetic sentence of exactly `length` characters ending in a full stop."""
    start = f"Sentence {n} says "
    return start + "x" * (length - len(start) - 1) + "."


def test_joins_hyphenated_line_breaks():
    passages = to_passages(["The fire-\nfighting team\nreached the deck."])

    assert len(passages) == 1
    assert passages[0][0] == 1
    assert "firefighting team" in passages[0][1]
    assert "\n" not in passages[0][1]


def test_passage_breaks_after_max_chars_on_sentence_end():
    sentences = [sentence(n) for n in (1, 2, 3)]
    assert all(len(s) == 100 for s in sentences)

    passages = to_passages([" ".join(sentences)], max_chars=220)

    assert [text for _, text in passages] == [f"{sentences[0]} {sentences[1]}", sentences[2]]


def test_long_sentence_is_never_cut():
    long = sentence(1, length=400)

    assert to_passages([long], max_chars=220) == [(1, long)]


def test_keeps_page_numbers():
    pages = ["The line parted.", "   ", "The crew were safe."]

    assert to_passages(pages) == [(1, "The line parted."), (3, "The crew were safe.")]


def test_group_by_page_caps_group_size():
    passages = [(1, f"p1 passage {i}.") for i in range(5)] + [(2, "p2 passage.")]

    groups = group_by_page(passages, size=2)

    assert groups == [
        (1, ["p1 passage 0.", "p1 passage 1."]),
        (1, ["p1 passage 2.", "p1 passage 3."]),
        (1, ["p1 passage 4."]),
        (2, ["p2 passage."]),
    ]
    assert group_by_page([]) == []


def test_pdf_pages_one_entry_per_page(tmp_path: Path):
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    path = tmp_path / "blank.pdf"
    with path.open("wb") as f:
        writer.write(f)

    assert pdf_pages(path) == ["", ""]


def test_an_encrypted_pdf_is_read(tmp_path: Path):
    # NTSB report PDFs are AES-encrypted with an empty user password; pypdf cannot decrypt AES
    # without the `cryptography` package, which Telltale does not depend on.
    path = tmp_path / "test-aes.pdf"
    write_aes_encrypted_pdf(path, "The towing vessel struck the bridge.")
    with pytest.raises(DependencyError):
        [page.extract_text() for page in PdfReader(path).pages]

    assert pdf_pages(path) == ["The towing vessel struck the bridge."]


def blank_pdf(path: Path, pages: int) -> Path:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as f:
        writer.write(f)
    return path


def test_scanned_pages_read_by_ocr_and_cached(tmp_path: Path):
    pdf = blank_pdf(tmp_path / "test-scan-abc.pdf", 2)
    calls: list[int] = []

    def fake_ocr(path: Path, index: int) -> str:
        calls.append(index)
        return "The tank was entered without a gas test." if index == 0 else ""

    pages = read_pages(pdf, ocr=fake_ocr, cache_dir=tmp_path / "ocr")

    assert pages == [("The tank was entered without a gas test.", True), ("", False)]
    assert calls == [0, 1]

    def must_not_run(path: Path, index: int) -> str:
        raise AssertionError("cached pages must not be read again")

    assert read_pages(pdf, ocr=must_not_run, cache_dir=tmp_path / "ocr") == pages


def test_ocr_failure_leaves_page_empty_and_carries_on(tmp_path: Path, caplog):
    pdf = blank_pdf(tmp_path / "test-scan-def.pdf", 1)

    def broken_ocr(path: Path, index: int) -> str:
        raise RuntimeError("model missing")

    with caplog.at_level("WARNING", logger="telltale.ingest.extract"):
        pages = read_pages(pdf, ocr=broken_ocr, cache_dir=tmp_path / "ocr")

    assert pages == [("", False)]
    assert "OCR failed on page 1" in caplog.text


def test_no_ocr_without_a_reader(tmp_path: Path):
    pdf = blank_pdf(tmp_path / "test-scan-ghi.pdf", 1)

    assert read_pages(pdf) == [("", False)]
