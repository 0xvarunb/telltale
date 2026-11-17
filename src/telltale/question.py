"""The checks a question passes before it is sent to the model, shared by the CLI and the page."""

import re

MAX_QUESTION = 500
EMPTY_QUESTION = "Type the job you are about to do."
TOO_LONG = f"Please keep it under {MAX_QUESTION} characters."

_WORD = re.compile(r"\w")


def question_problem(question: str) -> str | None:
    """The message to show instead of asking, or None when the question can be asked.

    Length is counted after trimming; a question with no letter or digit (only spaces or
    zero-width characters) counts as empty.
    """
    job = question.strip()
    if not _WORD.search(job):
        return EMPTY_QUESTION
    if len(job) > MAX_QUESTION:
        return TOO_LONG
    return None
