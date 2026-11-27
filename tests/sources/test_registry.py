import pytest

from telltale.sources import maib
from telltale.sources.registry import PUBLISHERS, Publisher, labelled_title, publisher


def test_publishers_give_name_and_credit():
    assert PUBLISHERS == {
        "maib": Publisher(
            id="maib",
            name="Marine Accident Investigation Branch",
            short="MAIB",
            source_credit="Crown copyright",
            footer_credit="UK MAIB, Crown copyright",
            licence_note="Open Government Licence v3.0",
        ),
        "atsb": Publisher(
            id="atsb",
            name="Australian Transport Safety Bureau",
            short="ATSB",
            source_credit="CC BY 4.0, ATSB",
            footer_credit="ATSB, CC BY 4.0",
            licence_note="Creative Commons Attribution 4.0 International",
        ),
        "ntsb": Publisher(
            id="ntsb",
            name="National Transportation Safety Board",
            short="NTSB",
            source_credit="US Government work, credited to the NTSB",
            footer_credit="US NTSB, US Government work",
            licence_note="not subject to copyright in the US",
        ),
        "tsib": Publisher(
            id="tsib",
            name="Transport Safety Investigation Bureau, Singapore",
            short="TSIB",
            source_credit="Source: Transport Safety Investigation Bureau, Singapore",
            footer_credit="Singapore TSIB",
            licence_note="public report, cited and linked",
        ),
    }


def test_unknown_publisher_is_named_in_the_error():
    with pytest.raises(KeyError, match="bsu"):
        publisher("bsu")


def test_maib_titles_keep_their_report_type_label():
    assert labelled_title("Grounding of Test Ship", "investigation-report", "maib") == (
        "Grounding of Test Ship"
    )
    assert labelled_title("Lifeboat hooks", "safety-bulletin", "maib") == (
        "Safety bulletin: Lifeboat hooks"
    )
    # The old import path gives the same title.
    assert maib.labelled_title("Lifeboat hooks", "safety-bulletin") == (
        "Safety bulletin: Lifeboat hooks"
    )


def test_other_publishers_titles_are_unchanged_for_now():
    # A report type that MAIB labels is not labelled for another publisher.
    assert labelled_title("Lifeboat hooks", "safety-bulletin", "ntsb") == "Lifeboat hooks"
    assert labelled_title("Collision in fog", "investigation-report", "tsib") == (
        "Collision in fog"
    )


def test_labelled_title_refuses_an_unknown_publisher():
    with pytest.raises(KeyError, match="bsu"):
        labelled_title("Collision in fog", "investigation-report", "bsu")
