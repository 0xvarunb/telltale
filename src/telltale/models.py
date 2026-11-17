"""Plain data shapes shared by every part of Telltale."""

import re
from dataclasses import dataclass

_PAGE = re.compile(r"#page=(\d+)$")


@dataclass
class ReportMeta:
    slug: str
    title: str
    page_url: str
    published: str
    occurred: str | None
    vessel_types: list[str]
    report_type: str
    pdf_url: str | None
    # The GOV.UK page body (HTML) of a report published with no PDF; not kept in the store.
    page_html: str | None = None


@dataclass
class Chunk:
    id: int
    report_slug: str
    report_title: str
    # The report PDF, or the GOV.UK page when the report has no PDF (`web_page`).
    url: str
    page: int
    passages: list[str]
    # True when the page was a scan and its text was read by OCR.
    ocr: bool = False
    # True when the text comes from the GOV.UK page, which has no page numbers.
    web_page: bool = False

    @property
    def source(self) -> str:
        return self.url if self.web_page else f"{self.url}#page={self.page}"


@dataclass
class Citation:
    report_title: str
    source: str
    cited_text: str

    @property
    def page(self) -> int | None:
        """The page number in `source` (`...pdf#page=N`), if it has one."""
        match = _PAGE.search(self.source)
        return int(match.group(1)) if match else None


@dataclass
class Sentence:
    text: str
    citations: list[Citation]

    @property
    def sourced(self) -> bool:
        return bool(self.citations)


@dataclass
class Answer:
    question: str
    sentences: list[Sentence]
    chunks: list[Chunk]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    stop_reason: str
