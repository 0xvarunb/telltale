"""Answer cards: one answer drawn as a phone-sized image (1080 x 1350, saved at 2x) for posts and
comments.

Everything on a card comes from the answer itself: the question, the chosen sentences word for
word with their source numbers or "no source" mark, the sources they cite, and the cost. A
no-answer result also lists the reports searched, by title and page only. Nothing is summarised or
reworded. Drawn with Pillow and the bundled brand fonts, so no browser is needed.
"""

import json
import random
import re
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from telltale.answer import (
    SEARCHED_CLOSING,
    is_no_answer,
    is_no_answer_result,
    reports_searched,
    stop_notice,
)
from telltale.answer.brief import REPORTS_SEARCHED
from telltale.ingest.store import OCR_NOTE
from telltale.models import Answer, Chunk, Citation, Sentence
from telltale.sources.registry import publisher

SCALE = 2
WIDTH, HEIGHT = 1080, 1350
MARGIN = 84
PAPER = (244, 241, 234)
INK = (19, 40, 59)
MUTED = (91, 103, 115)
RULE = (217, 210, 195)
ORANGE = (200, 68, 26)
GREEN = (47, 107, 74)
WHITE = (255, 255, 255)
MIN_BODY_SIZE = 22
MAX_BODY_SIZE = 40
SEARCHED_LABEL = "REPORTS SEARCHED (CLOSEST FIRST)"
MIN_SEARCHED_SIZE = 18
SEARCHED_LEAD = 10  # space above the reports-searched label, in card pixels


class CardError(Exception):
    """The card cannot be made as asked (bad sentence choice, or an answer with no sentences)."""


@dataclass
class Line:
    """One numbered sentence as the command line shows it."""

    number: int | None  # None for the plain "no reliable answer" sentence
    text: str
    sources: list[int]  # source numbers; empty means "no source" (unless plain)
    plain: bool = False


@dataclass
class Searched:
    """A report searched for a no-answer result: its title and page, and its publisher for the
    footer credit."""

    title: str
    page: int | None
    # Answers saved before publishers were recorded have none; every report then was MAIB's.
    source_id: str = "maib"


@dataclass
class Drawn:
    """What went on the card: the sentence numbers shown, how many were left off, and every
    piece of text drawn, top to bottom."""

    path: Path
    shown: list[int]
    left_out: int
    text: list[str] = field(default_factory=list)


@dataclass
class View:
    lines: list[Line]
    sources: dict[int, tuple[str, int | None, str]]  # number -> (title, page, link)
    notice: str | None
    publishers: dict[int, str] = field(default_factory=dict)  # number -> publisher id


def view(result: Answer) -> View:
    """Number sentences and sources exactly as `telltale ask` prints them."""
    numbers: dict[str, int] = {}
    details: dict[int, tuple[str, int | None, str]] = {}
    publishers: dict[int, str] = {}
    notice, notice_only = stop_notice(result)
    lines: list[Line] = []
    if not notice_only:
        n = 0
        for sentence in result.sentences:
            if is_no_answer(sentence):
                lines.append(Line(None, sentence.text, [], plain=True))
                continue
            refs: list[int] = []
            for citation in sentence.citations:
                number = numbers.setdefault(citation.source, len(numbers) + 1)
                details.setdefault(number, (citation.report_title, citation.page, citation.source))
                publishers.setdefault(number, citation.source_id)
                if number not in refs:
                    refs.append(number)
            n += 1
            lines.append(Line(n, sentence.text, refs))
    return View(lines=lines, sources=details, notice=notice, publishers=publishers)


# Saving an answer, so a card can be redrawn later without asking (and paying) again.


def to_json(result: Answer) -> str:
    data = {
        "question": result.question,
        "sentences": [
            {
                "text": s.text,
                "citations": [
                    {
                        "report_title": c.report_title,
                        "source": c.source,
                        "cited_text": c.cited_text,
                        "source_id": c.source_id,
                    }
                    for c in s.citations
                ],
            }
            for s in result.sentences
        ],
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
        "stop_reason": result.stop_reason,
        # Every retrieved chunk, in retrieval order, so any answer (a no-answer above all) can be
        # checked against what was searched.
        "searched": [
            {
                "report_title": c.report_title,
                "page": c.source_page,
                "source": c.source,
                "report_slug": c.report_slug,
                "source_id": c.source_id,
            }
            for c in result.chunks
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def from_json(text: str) -> Answer:
    data = json.loads(text)
    return Answer(
        question=data["question"],
        sentences=[
            Sentence(text=s["text"], citations=[Citation(**c) for c in s["citations"]])
            for s in data["sentences"]
        ],
        chunks=[],
        input_tokens=data["input_tokens"],
        output_tokens=data["output_tokens"],
        cost_usd=data["cost_usd"],
        stop_reason=data["stop_reason"],
    )


def searched_from_json(text: str) -> list[Searched]:
    """The reports searched, from a saved answer: the first appearance of each report, in
    retrieval order, as the command line lists them. Empty for a file saved before `searched`
    was added.

    A report is known by its slug; a file saved before slugs were added falls back to the title
    without the OCR note, which only some of a report's pages carry.
    """
    first: dict[str, Searched] = {}
    for item in json.loads(text).get("searched") or []:
        title = item["report_title"]
        key = item.get("report_slug") or title.removesuffix(OCR_NOTE)
        first.setdefault(
            key,
            Searched(
                title=title, page=item.get("page"), source_id=item.get("source_id", "maib")
            ),
        )
    return list(first.values())


def searched_from_chunks(chunks: list[Chunk]) -> list[Searched]:
    """The reports searched, from the retrieved chunks, as the command line lists them."""
    return [
        Searched(title=c.report_title, page=c.source_page, source_id=c.source_id)
        for c in reports_searched(chunks)
    ]


def footer_credit(source_ids: list[str]) -> str | None:
    """"Reports: " and the footer credit of each publisher, once each, in the order given; None
    when the list is empty."""
    credits = [publisher(source_id).footer_credit for source_id in dict.fromkeys(source_ids)]
    return "Reports: " + "; ".join(credits) if credits else None


def parse_selection(spec: str | None, highest: int) -> list[int] | None:
    """Sentence numbers from "1-4,7" (None means all). Raises CardError on a bad choice."""
    if spec is None or not spec.strip():
        return None
    chosen: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise CardError(f'"{part}" is not a sentence number or range (for example 1-4,7).')
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start or end > highest:
            raise CardError(f'"{part}" is outside the answer\'s sentences 1 to {highest}.')
        chosen.extend(n for n in range(start, end + 1) if n not in chosen)
    return chosen


# Drawing.


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = files("telltale") / "card_assets" / name
    return ImageFont.truetype(str(path), size * SCALE)


def _serif(size: int) -> ImageFont.FreeTypeFont:
    return _font("EBGaramond-SemiBold.ttf", size)


def _sans(size: int) -> ImageFont.FreeTypeFont:
    return _font("Geist-Medium.ttf", size)


def _width(font: ImageFont.FreeTypeFont, text: str) -> float:
    return font.getlength(text)


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: float, first_reserve: float = 0) -> list[str]:
    """Whole words per line; a single word longer than the line stays whole."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        limit = width - (first_reserve if not lines else 0)
        if current and _width(font, candidate) > limit:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


class _RecordingDraw(ImageDraw.ImageDraw):
    """Draws as usual and keeps every piece of text drawn, so tests can read the card."""

    def __init__(self, image: Image.Image) -> None:
        super().__init__(image)
        self.texts: list[str] = []

    def text(self, xy, text, *args, **kwargs):  # type: ignore[override]
        self.texts.append(text)
        return super().text(xy, text, *args, **kwargs)


def _paper(size: tuple[int, int]) -> Image.Image:
    """Off-white paper with a faint, fixed grain (the same on every card)."""
    image = Image.new("RGB", size, PAPER)
    rng = random.Random(7)
    grain = Image.new("L", (size[0] // 4, size[1] // 4))
    grain.putdata([rng.randint(0, 255) for _ in range(grain.width * grain.height)])
    grain = grain.resize(size, Image.Resampling.BILINEAR)
    shade = Image.new("RGB", size, (150, 132, 110))
    return Image.composite(shade, image, grain.point(lambda v: int(v * 0.06)))


def _marker_size(line: Line, font: ImageFont.FreeTypeFont) -> float:
    if line.plain:
        return 0
    pad = 12 * SCALE
    if not line.sources:
        return _width(font, "no source") + 2 * pad + 10 * SCALE
    return sum(_width(font, str(n)) + 2 * pad + 6 * SCALE for n in line.sources) + 4 * SCALE


def _layout(lines: list[Line], size: int, width: float) -> tuple[list[list[str]], float]:
    """Wrapped lines per sentence and the total height, at one body size."""
    body = _sans(size)
    tag = _sans(max(size - 8, 14))
    gap = int(size * 0.55) * SCALE
    line_height = int(size * 1.36) * SCALE
    number_room = _width(body, "00.") + 10 * SCALE
    wrapped: list[list[str]] = []
    total = 0.0
    for line in lines:
        text_width = width - number_room
        parts = _wrap(line.text, body, text_width)
        # Room for the marker at the end of the last line, else it goes on its own line.
        marker = _marker_size(line, tag)
        if marker and _width(body, parts[-1]) + 16 * SCALE + marker > text_width:
            parts.append("")
        wrapped.append(parts)
        total += len(parts) * line_height + gap
    return wrapped, total


def _wrap_report(report: Searched, font: ImageFont.FreeTypeFont, width: float) -> list[str]:
    """The title and its page, wrapped, with "page N" never split across lines."""
    if report.page is None:
        return _wrap(report.title, font, width)
    parts = _wrap(f"{report.title},", font, width)
    page = f"page {report.page}"
    if _width(font, f"{parts[-1]} {page}") <= width:
        parts[-1] = f"{parts[-1]} {page}"
    else:
        parts.append(page)
    return parts


_Row = tuple[str, ImageFont.FreeTypeFont, tuple[int, int, int], int]  # text, font, colour, advance


def _searched_block(reports: list[Searched], size: int, width: float, more: int) -> tuple[list[_Row], int]:
    """The rows of the reports-searched list and their total height, at one body size.

    Each report is its title and page, wrapped; `more` reports that did not fit are counted.
    """
    text_size = max(size - 6, MIN_SEARCHED_SIZE)
    font = _sans(text_size)
    line_height = int(text_size * 1.32) * SCALE
    entry_gap = int(text_size * 0.4) * SCALE
    rows: list[_Row] = [(SEARCHED_LABEL, _sans(16), MUTED, 26 * SCALE)]
    for report in reports:
        parts = _wrap_report(report, font, width)
        for i, part in enumerate(parts):
            rows.append((part, font, INK, line_height + (entry_gap if i == len(parts) - 1 else 0)))
    if more:
        rows.append((f"and {more} more in the saved answer.", font, MUTED, line_height + entry_gap))
    for part in _wrap(SEARCHED_CLOSING, font, width):
        rows.append((part, font, MUTED, line_height))
    return rows, SEARCHED_LEAD * SCALE + sum(row[3] for row in rows)


def render(
    result: Answer,
    out: Path,
    selection: list[int] | None = None,
    searched: list[Searched] | None = None,
) -> Drawn:
    """Draw the card for `result` (all sentences, or the numbers in `selection`) to `out` (PNG).

    A no-answer result also lists the reports searched: `searched` when given (from a saved
    answer), otherwise those of `result.chunks`.
    """
    v = view(result)
    if not v.lines and not v.notice:
        raise CardError("The answer has no sentences to put on a card.")
    numbered = [line for line in v.lines if line.number is not None]
    if selection is not None:
        lines = [line for line in v.lines if line.number in selection]
        left_out = len(numbered) - len(lines)
    else:
        lines = list(v.lines)
        left_out = 0

    w, h = WIDTH * SCALE, HEIGHT * SCALE
    m = MARGIN * SCALE
    inner = w - 2 * m
    image = _paper((w, h))
    draw = _RecordingDraw(image)

    # Kicker and question.
    y = 76 * SCALE
    kicker = _sans(20)
    draw.text((m, y), "TELLTALE  ·  ASKED BEFORE A JOB", font=kicker, fill=ORANGE)
    y += 44 * SCALE
    q_size = 46
    while True:
        q_font = _serif(q_size)
        q_lines = _wrap(f"“{result.question}”", q_font, inner)
        if len(q_lines) <= 4 or q_size <= 30:
            break
        q_size -= 2
    for part in q_lines[:5]:
        draw.text((m, y), part, font=q_font, fill=INK)
        y += int(q_size * 1.12) * SCALE
    y += 22 * SCALE
    draw.rectangle((m, y, w - m, y + 2 * SCALE), fill=INK)
    y += 30 * SCALE

    # Sources cited by the chosen sentences (in their original numbers).
    cited = sorted({n for line in lines for n in line.sources})
    src_font = _sans(18)
    src_lines: list[str] = []
    for n in cited:
        title, page, _ = v.sources[n]
        where = f", page {page}" if page is not None else ""
        src_lines.extend(_wrap(f"[{n}] {title}{where}", src_font, inner)[:2])
    src_height = (len(src_lines) * 27 + (40 if src_lines else 0)) * SCALE

    # Footer.
    footer_height = 96 * SCALE
    # Room is always kept for the "more sentences" note, which a long answer may need.
    note_height = 34 * SCALE + (40 if v.notice else 0) * SCALE
    room = h - y - src_height - footer_height - note_height - 24 * SCALE

    # A no-answer result lists the first reports searched under its sentence.
    if is_no_answer_result(result):
        found = searched if searched is not None else searched_from_chunks(result.chunks)
        listed = found[:REPORTS_SEARCHED]
    else:
        listed = []
    reports = listed

    def measure(chosen: list[Line], at: int) -> tuple[list[list[str]], float]:
        wrapped_, total_ = _layout(chosen, at, inner)
        if listed:
            total_ += _searched_block(reports, at, inner, len(listed) - len(reports))[1]
        return wrapped_, total_

    size = 30
    wrapped, total = measure(lines, size)
    # A short card gets larger text, up to MAX_BODY_SIZE, rather than an empty band.
    while total <= room and size < MAX_BODY_SIZE:
        bigger, bigger_total = measure(lines, size + 1)
        if bigger_total > room:
            break
        size, wrapped, total = size + 1, bigger, bigger_total
    while total > room and size > MIN_BODY_SIZE:
        size -= 1
        wrapped, total = measure(lines, size)
    # Then fewer reports searched (the rest are counted), then fewer sentences.
    while total > room and len(reports) > 1:
        reports = reports[:-1]
        wrapped, total = measure(lines, size)
    shown = len(lines)
    while total > room and shown > 1:
        shown -= 1
        wrapped, total = measure(lines[:shown], size)
    dropped = lines[shown:]
    lines = lines[:shown]
    left_out += sum(1 for line in dropped if line.number is not None)

    body = _sans(size)
    tag = _sans(max(size - 8, 14))
    line_height = int(size * 1.36) * SCALE
    gap = int(size * 0.55) * SCALE
    number_room = _width(body, "00.") + 10 * SCALE
    if v.notice:
        draw.text((m, y), v.notice, font=_sans(20), fill=ORANGE)
        y += 40 * SCALE
    for line, parts in zip(lines, wrapped):
        if line.number is not None:
            draw.text((m, y), f"{line.number}.", font=body, fill=MUTED)
        x0 = m + (number_room if line.number is not None else 0)
        for i, part in enumerate(parts):
            draw.text((x0, y), part, font=body, fill=INK)
            if i == len(parts) - 1 and not line.plain:
                mx = x0 + (_width(body, part) + 16 * SCALE if part else 0)
                _draw_marker(draw, line, mx, y, tag, size)
            y += line_height
        y += gap
    if left_out:
        draw.text((m, y), f"{left_out} more sentence{'s' if left_out != 1 else ''} in the full answer.",
                  font=_sans(19), fill=MUTED)
        y += 34 * SCALE
    if listed:
        rows, _ = _searched_block(reports, size, inner, len(listed) - len(reports))
        y += SEARCHED_LEAD * SCALE
        for text, font, fill, advance in rows:
            draw.text((m, y), text, font=font, fill=fill)
            y += advance

    # Sources block, then the footer.
    y = h - footer_height - src_height
    if src_lines:
        draw.rectangle((m, y, w - m, y + 1 * SCALE), fill=RULE)
        y += 14 * SCALE
        draw.text((m, y), "SOURCES", font=_sans(16), fill=MUTED)
        y += 26 * SCALE
        for part in src_lines:
            draw.text((m, y), part, font=src_font, fill=INK)
            y += 27 * SCALE

    y = h - footer_height + 18 * SCALE
    logo = Image.open(files("telltale") / "card_assets" / "telltale-logo.png").convert("RGBA")
    logo_w = 190 * SCALE
    logo = logo.resize((logo_w, int(logo.height * logo_w / logo.width)), Image.Resampling.LANCZOS)
    image.paste(logo, (m - 8 * SCALE, y), logo)
    small = _sans(17)
    right = [f"Cost of this answer: ${result.cost_usd:.4f}"]
    # A card with nothing from a report still carries the MAIB credit, as every card did before
    # other publishers were added.
    credit = footer_credit(
        [v.publishers[n] for line in lines for n in line.sources]
        + [report.source_id for report in reports]
    ) or footer_credit(["maib"])
    right.append(credit)
    ty = y + 6 * SCALE
    for text in right:
        draw.text((w - m - _width(small, text), ty), text, font=small, fill=MUTED)
        ty += 26 * SCALE

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out, "PNG")
    return Drawn(out, [line.number for line in lines if line.number is not None], left_out,
                 draw.texts)


def _draw_marker(draw: ImageDraw.ImageDraw, line: Line, x: float, y: float,
                 font: ImageFont.FreeTypeFont, size: int) -> None:
    pad = 12 * SCALE
    top = y + int(size * 0.12) * SCALE
    bottom = top + int(size * 1.0) * SCALE
    if not line.sources:
        label = "no source"
        width = _width(font, label) + 2 * pad
        draw.rounded_rectangle((x, top, x + width, bottom), radius=(bottom - top) // 2,
                               outline=ORANGE, width=2 * SCALE, fill=PAPER)
        draw.text((x + pad, (top + bottom) / 2), label, font=font, fill=ORANGE, anchor="lm")
        return
    for n in line.sources:
        label = str(n)
        width = _width(font, label) + 2 * pad
        draw.rounded_rectangle((x, top, x + width, bottom), radius=6 * SCALE, fill=GREEN)
        draw.text((x + width / 2, (top + bottom) / 2), label, font=font, fill=WHITE, anchor="mm")
        x += width + 6 * SCALE
