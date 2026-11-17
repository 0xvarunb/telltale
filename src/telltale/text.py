"""Split report and answer text into sentences without breaking on numbers or abbreviations."""

import re

# Lower-case words that end in a full stop but do not end a sentence.
ABBREVIATIONS = {
    "e.g", "i.e", "approx", "fig", "figs", "para", "paras", "cf", "vs", "viz",
    "mr", "mrs", "ms", "dr", "capt", "ref", "vol",
}
# Matched only with a capital: "switched off." and "for 30 sec." end ordinary sentences.
CAPITALISED_ABBREVIATIONS = {"St", "Off", "Sec"}
# "No. 3(P)" and "Nos. 2 and 3": only before a number, so "The answer was no. Then..." still splits.
NUMBER_ABBREVIATIONS = {"no", "nos"}

# End punctuation (with any closing quote or bracket), whitespace, then what can start a sentence.
# A decimal such as 15.87m has no whitespace after its point, so it is never a candidate.
BOUNDARY = re.compile(r"([.!?]+[\"'”’)\]]*)\s+(?=[\"'“‘(\[]?[A-Z0-9])")
CLOSERS = "\"'”’)]"
PARAGRAPH_BREAK = re.compile(r"\n[ \t\r\f\v]*\n\s*")
LAST_WORD = re.compile(r"(\S+)$")
INITIALS = re.compile(r"(?:[A-Z]\.)*[A-Z]")
# After an initial, these words start a new sentence ("hold B. The fire spread."), since no name
# begins with them. Another initial ("J. A. Name") is followed by a full stop, so never matches.
SENTENCE_OPENERS = {
    "A", "An", "The", "This", "That", "These", "Those", "It", "Its", "He", "She", "They",
    "Their", "His", "Her", "We", "There", "In", "On", "At", "After", "Before", "When", "While",
    "During", "However", "Then", "As", "Both", "No", "Nobody", "None", "Each", "All",
}
NEXT_WORD = re.compile(r"[\"'“‘(\[]?([A-Z][a-z]*)\b(?!\.)")


def _keeps_going(text: str, start: int, boundary: re.Match) -> bool:
    """True when the full stop at this boundary belongs to an abbreviation or an initial."""
    if boundary.group(1).rstrip(CLOSERS) != ".":
        return False
    before = LAST_WORD.search(text, start, boundary.start())
    if not before:
        return False
    word = before.group(1).lstrip("\"'“‘([")
    if word.lower() in ABBREVIATIONS or word in CAPITALISED_ABBREVIATIONS:
        return True
    if INITIALS.fullmatch(word):
        after = NEXT_WORD.match(text, boundary.end())
        return not (after and after.group(1) in SENTENCE_OPENERS)
    return word.lower() in NUMBER_ABBREVIATIONS and text[boundary.end()].isdigit()


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) offsets into `text` of each sentence, trimmed of surrounding whitespace.

    A blank line always ends a sentence; a single line break is just a space.
    """
    spans: list[tuple[int, int]] = []
    cuts = [0]
    for paragraph in PARAGRAPH_BREAK.finditer(text):
        cuts += [paragraph.start(), paragraph.end()]
    cuts.append(len(text))

    for para_start, para_end in zip(cuts[::2], cuts[1::2]):
        start = para_start
        for boundary in BOUNDARY.finditer(text, para_start, para_end):
            if _keeps_going(text, start, boundary):
                continue
            spans.append((start, boundary.end(1)))
            start = boundary.end()
        spans.append((start, para_end))

    trimmed = []
    for start, end in spans:
        piece = text[start:end]
        start += len(piece) - len(piece.lstrip())
        end -= len(piece) - len(piece.rstrip())
        if start < end:
            trimmed.append((start, end))
    return trimmed


def split_sentences(text: str) -> list[str]:
    """Sentences of `text`, each with its whitespace collapsed to single spaces."""
    return [" ".join(text[start:end].split()) for start, end in sentence_spans(text)]
