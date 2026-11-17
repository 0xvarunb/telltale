"""One real OCR read of a synthetic scanned page: the model ships inside the package, so this
runs offline. It takes a few seconds."""

from pathlib import Path

import numpy as np

from telltale.ingest.extract import read_pages
from telltale.ingest.ocr import LocalOcr


def scanned_pdf(path: Path, lines: list[str]) -> Path:
    """A one-page PDF holding only a picture of typed text: no text layer."""
    import cv2
    import pypdfium2 as pdfium

    picture = np.full((1100, 850, 3), 255, np.uint8)
    for n, line in enumerate(lines):
        cv2.putText(picture, line, (40, 120 + 60 * n), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    jpeg = path.with_suffix(".jpg")
    cv2.imwrite(str(jpeg), picture)
    document = pdfium.PdfDocument.new()
    page = document.new_page(612, 792)
    image = pdfium.PdfImage.new(document)
    image.load_jpeg(str(jpeg))
    image.set_matrix(pdfium.PdfMatrix().scale(612, 792))
    page.insert_obj(image)
    page.gen_content()
    document.save(str(path))
    return path


def test_local_ocr_reads_a_scanned_page(tmp_path: Path):
    lines = ["The chief officer entered the No 3 ballast tank.", "The oxygen reading was 15.8 per cent."]
    pdf = scanned_pdf(tmp_path / "test-scanned-page.pdf", lines)

    [(text, by_ocr)] = read_pages(pdf, ocr=LocalOcr(), cache_dir=tmp_path / "ocr")

    assert by_ocr is True
    assert "No 3 ballast tank" in text
    assert "15.8 per cent" in text
