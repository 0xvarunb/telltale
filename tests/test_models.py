from telltale.models import Chunk, Citation, Sentence


def test_chunk_source_points_at_pdf_page():
    chunk = Chunk(
        id=1,
        report_slug="example-report",
        report_title="Example report",
        url="https://example.org/report.pdf",
        page=7,
        passages=["A passage."],
    )

    assert chunk.source == "https://example.org/report.pdf#page=7"


def test_sentence_is_sourced_only_with_citations():
    citation = Citation(
        report_title="Example report",
        source="https://example.org/report.pdf#page=7",
        cited_text="A passage.",
    )

    assert Sentence(text="Cited.", citations=[citation]).sourced is True
    assert Sentence(text="Uncited.", citations=[]).sourced is False


def test_citation_page_comes_from_its_source():
    def cite(source: str) -> Citation:
        return Citation(report_title="Example report", source=source, cited_text="A passage.")

    assert cite("https://example.org/report.pdf#page=12").page == 12
    assert cite("https://example.org/report.pdf").page is None


def test_web_page_chunk_source_is_the_page_itself():
    from telltale.answer.prompt import result_title

    page = "https://www.gov.uk/maib-reports/test-report"
    chunk = Chunk(
        id=1, report_slug="test-report", report_title="Preliminary examination: Test report",
        url=page, page=1, passages=["Text."], web_page=True,
    )
    assert chunk.source == page
    assert Citation(report_title="x", source=chunk.source, cited_text="Text.").page is None
    assert result_title(chunk) == "Preliminary examination: Test report (GOV.UK page)"
