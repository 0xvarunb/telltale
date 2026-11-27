"""The question page: one form, and the cited answer shown under it with its sources."""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from starlette.exceptions import HTTPException

from telltale.answer import (
    SEARCHED_CLOSING,
    is_no_answer,
    is_no_answer_result,
    reports_searched,
    stop_notice,
)
from telltale.models import Answer
from telltale.question import EMPTY_QUESTION, TOO_LONG, question_problem
from telltale.sources.registry import publisher

HERE = Path(__file__).parent
OTHER_SITE = "This question came from another website, so it was not asked. Ask it on this page."
FAILED = (
    "Sorry, no answer could be made just now. Check the internet connection and try again "
    "in a minute."
)

logger = logging.getLogger(__name__)


@dataclass
class Line:
    """One sentence as shown: its source numbers, or plain (the no-answer sentence)."""

    text: str
    marks: list[int]
    plain: bool = False


@dataclass
class SourceEntry:
    number: int
    title: str
    page: int | None
    link: str | None
    credit: str  # the publisher's source credit
    passages: list[str] = field(default_factory=list)


@dataclass
class SearchedEntry:
    """A report searched for a no-answer result: its title, page and link, nothing more."""

    title: str
    page: int | None
    link: str | None


@dataclass
class View:
    lines: list[Line]
    sources: list[SourceEntry]
    notice: str | None
    cost: str
    searched: list[SearchedEntry] = field(default_factory=list)
    searched_closing: str = SEARCHED_CLOSING
    # The source credit of each publisher of the reports searched, once each, in their order.
    searched_credits: list[str] = field(default_factory=list)


def _link(source: str) -> str | None:
    """The source as a link, only if it is a web address."""
    return source if source.startswith(("https://", "http://")) else None


def to_view(result: Answer) -> View:
    """Number the sources in order of first use (one number per PDF page), as the CLI does,
    and list under each the passages actually cited, once each. A no-answer result also lists
    the first reports searched, as the CLI does."""
    notice, notice_only = stop_notice(result)
    lines: list[Line] = []
    entries: dict[str, SourceEntry] = {}
    if not notice_only:
        for sentence in result.sentences:
            if is_no_answer(sentence):
                lines.append(Line(text=sentence.text, marks=[], plain=True))
                continue
            marks: list[int] = []
            for citation in sentence.citations:
                entry = entries.get(citation.source)
                if entry is None:
                    entry = SourceEntry(
                        number=len(entries) + 1,
                        title=citation.report_title,
                        page=citation.page,
                        link=_link(citation.source),
                        credit=publisher(citation.source_id).source_credit,
                    )
                    entries[citation.source] = entry
                if citation.cited_text not in entry.passages:
                    entry.passages.append(citation.cited_text)
                if entry.number not in marks:
                    marks.append(entry.number)
            lines.append(Line(text=sentence.text, marks=marks))
    cost = (
        f"Cost ${result.cost_usd:.4f} "
        f"({result.input_tokens:,} tokens in, {result.output_tokens:,} out)"
    )
    searched_chunks = reports_searched(result.chunks) if is_no_answer_result(result) else []
    searched = [
        SearchedEntry(title=chunk.report_title, page=chunk.source_page, link=_link(chunk.source))
        for chunk in searched_chunks
    ]
    searched_credits = [
        publisher(source_id).source_credit
        for source_id in dict.fromkeys(chunk.source_id for chunk in searched_chunks)
    ]
    return View(
        lines=lines,
        sources=list(entries.values()),
        notice=notice,
        cost=cost,
        searched=searched,
        searched_credits=searched_credits,
    )


def _same_origin(request: Request) -> bool:
    """True unless the browser says the form was sent from another site.

    A request with no Origin header (curl, tests, some older browsers) is allowed.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()


def _names(shorts: list[str]) -> str:
    """"MAIB", "MAIB and NTSB", "MAIB, NTSB and TSIB"."""
    return shorts[0] if len(shorts) == 1 else f"{', '.join(shorts[:-1])} and {shorts[-1]}"


def create_app(
    answer_fn: Callable[[str], Answer], publisher_ids: Sequence[str] = ("maib",)
) -> FastAPI:
    """The page, answering each question with `answer_fn`. The footer credits each publisher in
    `publisher_ids` (those with reports in the store); none credited when it is empty. The
    limits page names and credits the same publishers (MAIB when there are none, the default
    source of `telltale ingest`).

    Raises KeyError naming an unknown publisher id.
    """
    app = FastAPI(title="Telltale", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=True)
    templates.globals["reports_credit"] = "; ".join(
        publisher(source_id).footer_credit for source_id in publisher_ids
    )
    page = templates.get_template("index.html")
    limits_page = templates.get_template("limits.html")
    covered = list(publisher_ids) or ["maib"]
    limits_html = limits_page.render(
        covered=covered, covered_names=_names([publisher(p).short for p in covered])
    )

    def render(question: str = "", message: str | None = None, view: View | None = None):
        return HTMLResponse(page.render(question=question, message=message, view=view))

    @app.get("/", response_class=HTMLResponse)
    def form() -> HTMLResponse:
        return render()

    @app.get("/limits", response_class=HTMLResponse)
    def limits() -> HTMLResponse:
        """What this tool can't do: the same text as docs/limits.md."""
        return HTMLResponse(limits_html)

    @app.get("/ask")
    def ask_by_link() -> RedirectResponse:
        return RedirectResponse("/", status_code=303)

    @app.post("/ask", response_class=HTMLResponse)
    async def ask(request: Request) -> HTMLResponse:
        # Another website could make the officer's browser post here and spend credit.
        if not _same_origin(request):
            return render(message=OTHER_SITE)
        try:
            async with request.form() as fields:
                question = fields.get("question", "")
        except HTTPException as error:
            # Starlette refuses a field over 1 MB (and other broken forms) before we see it.
            too_big = "exceeded maximum size" in str(error.detail)
            return render(message=TOO_LONG if too_big else EMPTY_QUESTION)
        if not isinstance(question, str):  # a file upload
            return render(message=EMPTY_QUESTION)
        problem = question_problem(question)
        if problem is not None:
            return render(question, problem)
        job = question.strip()
        try:
            result = await run_in_threadpool(answer_fn, job)
        except Exception:
            logger.exception("No answer for the question")
            return render(question, FAILED)
        return render(question, view=to_view(result))

    return app
