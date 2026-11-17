"""Read the text of scanned PDF pages: render the page, then read it with a local OCR model.

Runs on CPU with no internet: pypdfium2 renders the page and RapidOCR (ONNX models shipped
inside the package) reads it. OCR can misread words and figures, so every passage read this
way is marked wherever it is cited (see `Store.chunks`).
"""

from pathlib import Path
from typing import Protocol

OCR_DPI = 200
# Lines the model is unsure of are left out rather than risk a misread figure.
MIN_CONFIDENCE = 0.6


class PageReader(Protocol):
    def __call__(self, pdf: Path, page_index: int) -> str:
        """The text of one page (0-based index), "" if none could be read."""
        ...


class LocalOcr:
    """OCR for one page at a time. The model loads on first use, so creating this is free."""

    def __init__(self) -> None:
        self._engine = None

    def __call__(self, pdf: Path, page_index: int) -> str:
        import pypdfium2 as pdfium

        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
        document = pdfium.PdfDocument(pdf)
        try:
            image = document[page_index].render(scale=OCR_DPI / 72).to_numpy()
        finally:
            document.close()
        lines, _ = self._engine(image)
        return " ".join(text for _, text, confidence in lines or [] if confidence >= MIN_CONFIDENCE)
