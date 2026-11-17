"""The system prompt and request for a cited answer from retrieved report passages."""

from telltale.models import Chunk

# The one sentence for a job the reports do not cover. Shown plainly, with no "no source" mark.
NO_ANSWER = "There is no reliable answer in these reports for that job."

# The spike's final wording (spikes/2026-10-30-cited-answer/src/run.py), plus: a clause so a job
# the reports do not cover gets exactly NO_ANSWER, not the closest case stretched to fit; a clause
# that judges a case by the job, not the ship type (Ruling 27: the first real ballast-tank question
# turned down a ballast-tank entry on a container ship because it asked about a bulk carrier); a
# sentence for spec rule 5 (never "safe to proceed"); after the second try (Ruling 28), a
# citation on every sentence, the report's own certainty, and no sentences about the results; and
# after the three-question baseline (Ruling 29), each point once, no title-only sentences, no
# figure captions, and no explanations the results do not give.
SYSTEM_PROMPT = (
    "You brief officers on merchant ships before a job. Use only the search results provided. "
    "Add nothing from your own knowledge: no procedures, regulations, figures or advice the "
    "results do not state. "
    "For each relevant case, say first what happened and how it ended, then how it happened, "
    "then what the report says would have prevented it. No blame. Plain English, short "
    "paragraphs, no checklist, nothing to sign. "
    "Never say or imply that a job is safe or safe to proceed, and never stand in for the permit "
    "to work, the risk assessment, the SMS or the office's permission. "
    "Judge each result by the job and its hazards, not by the type of ship: a case of the same "
    "job on another type of ship is relevant, so give it and say what type of ship it was. "
    "If no result is about the job, reply with exactly this sentence and nothing else: "
    f"\"{NO_ANSWER}\" Do not offer the closest case or stretch a case about a different job "
    "to fit. "
    "Every sentence must come from a search result and carry its citation, even when the "
    "sentence before it cites the same passage. Keep the report's own words for its findings and "
    "for how sure it is. "
    "Write only about the cases: never about the search results themselves, which of them are "
    "relevant, what they cover or what they do not say. "
    "Make each point once: never write a sentence of your own and then repeat its point in the "
    "report's words; write the one sentence and cite it. Never write a sentence that only repeats "
    "a report's title or the line that introduces each result. Leave out figure labels and "
    "captions. Never explain a term, a piece of equipment or a procedure unless a result "
    "explains it. "
    "No headings, no introduction, no summary of what the results contain; name the ship inside "
    "the first sentence about it."
)

MAX_TOKENS = 8000


def result_title(chunk: Chunk) -> str:
    where = "GOV.UK page" if chunk.web_page else f"page {chunk.page}"
    return f"{chunk.report_title} ({where})"


def build_request(question: str, chunks: list[Chunk], model: str) -> dict:
    """Arguments for `client.messages.create`: one search result per chunk, then the question.

    Never sends temperature, top_p or top_k.
    """
    results = [
        {
            "type": "search_result",
            "source": chunk.source,
            "title": result_title(chunk),
            "content": [
                {
                    "type": "text",
                    "text": f"This passage is from the MAIB report: {chunk.report_title}.",
                },
                *({"type": "text", "text": passage} for passage in chunk.passages),
            ],
            "citations": {"enabled": True},
        }
        for chunk in chunks
    ]
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": [*results, {"type": "text", "text": question}]}
        ],
    }
