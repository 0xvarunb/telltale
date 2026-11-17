"""Sort reports into shipboard departments from words in their official titles.

A first pass, made to be checked by a seafarer: every rule is a plain list of words or phrases,
a report can belong to more than one department, and each placement records the words that
caused it. Nothing is inferred beyond the title, so a title that matches no rule stays unsorted.
Words that name the ship type ("general cargo vessel", "passenger ferry") are deliberately not
rules: they say what the ship is, not where on board the accident happened.
"""

import re
from dataclasses import dataclass

BRIDGE = "Bridge and navigation"
DECK = "Deck"
ENGINE = "Engine"
CATERING = "Catering and accommodation"

# Whole words or phrases, matched case-insensitively against the title.
RULES: dict[str, list[str]] = {
    BRIDGE: [
        "collision", "collisions", "grounding", "groundings", "grounded", "close-quarters",
        "close quarters", "near-miss", "near miss", "contact", "loss of control",
        "dragging anchor", "steering failure", "steering control failure", "heavy weather",
        "large wave", "large waves", "wash waves", "navigation", "uncharted",
    ],
    DECK: [
        "mooring", "unmooring", "moorings", "hawser", "towline", "tow wire", "towing", "tow",
        "girting", "anchor", "windlass", "cargo operations", "cargo shift", "shift of cargo",
        "cargo collapse", "cargo hold", "cargo hatch", "cargo hopper", "cargo tank",
        "cargo containers", "loss of cargo", "cargo vapours", "containers", "hatch",
        "hatch cover", "hold", "loading", "discharging", "unloading", "fumigated", "crane", "lifting", "derrick",
        "gantry", "sheerleg", "lifeboat", "rescue boat", "fast rescue craft", "davit",
        "liferaft", "evacuation", "tender", "pilot ladder", "pilot transfer",
        "accommodation ladder", "gangway", "enclosed space", "confined space", "ballast tank",
        "stern ramp", "bow door", "bow visor", "linkspan", "watertight door", "lashing",
        "person overboard", "persons overboard", "man overboard", "washed overboard",
        "fall overboard", "deck",
    ],
    ENGINE: [
        "engine room", "engine-room", "engine", "machinery", "main engine", "propulsion",
        "blackout", "electrical", "switchboard", "boiler", "exhaust", "generator", "capacitor",
        "harmonic filter", "controllable pitch propeller", "propeller", "podded propulsor",
        "steering", "pump", "fuel", "bunker", "purifier", "pumproom", "carbon dioxide", "co2",
        "fixed fire extinguishing", "hydraulic", "steam",
    ],
    CATERING: [
        "galley", "swimming pool", "balcony", "cabin", "accommodation area", "stairs",
        "restaurant", "laundry",
    ],
}


# Phrases removed before matching because they name the vessel, not the work: a
# "self-propelled crane" is a crane vessel, not a crane operation.
VESSEL_TYPES = ["self-propelled crane", "self-propelled floating sheerleg crane", "crane barge"]


def _pattern(words: list[str]) -> re.Pattern[str]:
    alternatives = sorted((re.escape(w) for w in words), key=len, reverse=True)
    return re.compile(r"(?<![\w-])(" + "|".join(alternatives) + r")(?![\w-])", re.IGNORECASE)


PATTERNS = {department: _pattern(words) for department, words in RULES.items()}
VESSEL_TYPE_PATTERN = _pattern(VESSEL_TYPES)


@dataclass
class Placement:
    department: str
    matched: list[str]


def departments(title: str) -> list[Placement]:
    """The departments a report title points to, with the words that matched, in rule order."""
    text = VESSEL_TYPE_PATTERN.sub(" ", title)
    placements = []
    for department, pattern in PATTERNS.items():
        matched = sorted({m.group(1).lower() for m in pattern.finditer(text)})
        if matched:
            placements.append(Placement(department, matched))
    return placements
