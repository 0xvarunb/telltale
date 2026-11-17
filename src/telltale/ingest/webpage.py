"""Cut the text of a GOV.UK report page into citable passages.

MAIB publishes a completed preliminary examination as the text of its GOV.UK page, with no PDF
(all 180 such reports on 6 Nov 2026). The page body is HTML: each paragraph, list item and
heading is read as its own block, so sentences never run across blocks. Figures are left
out: pictures are not used anywhere in the tool. A table row is read as one block with its cells
joined by ": ", so the ship particulars read "Flag: St Kitts and Nevis".
"""

from html.parser import HTMLParser

from telltale.text import split_sentences

BLOCK_TAGS = {
    "p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "blockquote",
    "caption", "pre", "div", "br", "ul", "ol", "table",
}
CELL_TAGS = {"td", "th"}
SKIPPED_TAGS = {"figure", "script", "style", "svg"}
# Fragments this short (stray bullets) are not worth citing, as in PDF text.
MIN_SENTENCE_CHARS = 3


class _Blocks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._text: list[str] = []
        self._skipping = 0
        # The cells read so far in the current table row, or None outside a row.
        self._cells: list[str] | None = None

    def _flush(self) -> None:
        text = " ".join("".join(self._text).split())
        self._text = []
        if not text:
            return
        if self._cells is not None:
            self._cells.append(text)
        else:
            self.blocks.append(text)

    def _end_row(self) -> None:
        self._flush()
        cells, self._cells = self._cells, None
        if cells:
            self.blocks.append(": ".join(cells))

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in SKIPPED_TAGS:
            self._skipping += 1
        elif tag == "tr":
            self._end_row()
            self._flush()
            self._cells = []
        elif tag in BLOCK_TAGS or tag in CELL_TAGS:
            self._flush()

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_TAGS:
            self._skipping = max(0, self._skipping - 1)
        elif tag == "tr" or (tag == "table" and self._cells is not None):
            self._end_row()
        elif tag in BLOCK_TAGS or tag in CELL_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self._text.append(data)


def page_blocks(html: str) -> list[str]:
    """The text blocks of an HTML page body, in order, whitespace collapsed."""
    parser = _Blocks()
    parser.feed(html)
    parser.close()
    parser._end_row()
    return parser.blocks


def page_passages(html: str, max_chars: int = 220) -> list[str]:
    """Passages of whole sentences, joined until the next would pass `max_chars`, as for a PDF
    page. A heading stays a sentence of its own, so it is joined to the text after it."""
    sentences = [
        sentence
        for block in page_blocks(html)
        for sentence in split_sentences(block)
        if len(sentence) >= MIN_SENTENCE_CHARS
    ]
    passages: list[str] = []
    buffer = ""
    for sentence in sentences:
        if buffer and len(buffer) + 1 + len(sentence) > max_chars:
            passages.append(buffer)
            buffer = sentence
        else:
            buffer = f"{buffer} {sentence}" if buffer else sentence
    if buffer:
        passages.append(buffer)
    return passages
