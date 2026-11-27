"""The bodies whose accident reports Telltale stores, and how each one is credited."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Publisher:
    id: str
    name: str
    short: str
    # Credit printed beside a passage or source on the command line.
    source_credit: str
    # Credit printed in the footer of an answer card.
    footer_credit: str
    licence_note: str


PUBLISHERS: dict[str, Publisher] = {
    p.id: p
    for p in (
        Publisher(
            id="maib",
            name="Marine Accident Investigation Branch",
            short="MAIB",
            source_credit="Crown copyright",
            footer_credit="UK MAIB, Crown copyright",
            licence_note="Open Government Licence v3.0",
        ),
        Publisher(
            id="atsb",
            name="Australian Transport Safety Bureau",
            short="ATSB",
            source_credit="CC BY 4.0, ATSB",
            footer_credit="ATSB, CC BY 4.0",
            licence_note="Creative Commons Attribution 4.0 International",
        ),
        Publisher(
            id="ntsb",
            name="National Transportation Safety Board",
            short="NTSB",
            source_credit="US Government work, credited to the NTSB",
            footer_credit="US NTSB, US Government work",
            licence_note="not subject to copyright in the US",
        ),
        Publisher(
            id="tsib",
            name="Transport Safety Investigation Bureau, Singapore",
            short="TSIB",
            source_credit="Source: Transport Safety Investigation Bureau, Singapore",
            footer_credit="Singapore TSIB",
            licence_note="public report, cited and linked",
        ),
    )
}

# Report types each publisher's listing keeps, with the label shown before a report's title so a
# reader never mistakes a bulletin or a preliminary examination for a full investigation (MAIB:
# Ruling 23). An empty label is a full investigation. A publisher missing here labels nothing.
REPORT_TYPE_LABELS: dict[str, dict[str, str]] = {
    "maib": {
        "investigation-report": "",
        "investigation-report-on-behalf-of-red-ensign-group": "Red Ensign Group investigation",
        "safety-bulletin": "Safety bulletin",
        "completed-preliminary-examination": "Preliminary examination",
    },
}


def publisher(source_id: str) -> Publisher:
    """The publisher with this id; raises KeyError naming an unknown id."""
    try:
        return PUBLISHERS[source_id]
    except KeyError:
        raise KeyError(f"no publisher {source_id!r}; known: {', '.join(PUBLISHERS)}") from None


def labelled_title(title: str, report_type: str, source_id: str) -> str:
    """The title as shown in answers: prefixed with its type unless it is a full investigation.

    Raises KeyError naming an unknown publisher id.
    """
    publisher(source_id)
    label = REPORT_TYPE_LABELS.get(source_id, {}).get(report_type, "")
    return f"{label}: {title}" if label else title
