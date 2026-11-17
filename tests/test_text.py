from telltale.text import split_sentences


def test_split_keeps_decimals_and_abbreviations():
    text = (
        "The vessel was 15.87m long and No. 3(P) tank flooded, e.g. through a fracture. "
        "Nobody was hurt."
    )

    assert split_sentences(text) == [
        "The vessel was 15.87m long and No. 3(P) tank flooded, e.g. through a fracture.",
        "Nobody was hurt.",
    ]


def test_split_keeps_other_abbreviations_and_initials():
    text = (
        "The bosun, i.e. the senior rating, measured approx. 2.5 tonnes. "
        "See Fig. 4 and para. 2.3 for the layout. "
        "The master, J. R. Testperson, and the M.V. Example crew agreed. "
        "Was the hatch open? Yes! It was."
    )

    assert split_sentences(text) == [
        "The bosun, i.e. the senior rating, measured approx. 2.5 tonnes.",
        "See Fig. 4 and para. 2.3 for the layout.",
        "The master, J. R. Testperson, and the M.V. Example crew agreed.",
        "Was the hatch open?",
        "Yes!",
        "It was.",
    ]


def test_split_after_a_number_that_ends_a_sentence():
    assert split_sentences("The crew numbered 15. Three were hurt.") == [
        "The crew numbered 15.",
        "Three were hurt.",
    ]


def test_split_normalises_whitespace_and_handles_empty():
    assert split_sentences("") == []
    assert split_sentences("   \n\t ") == []
    assert split_sentences("  The   line\nparted.\n  It  struck the deck. ") == [
        "The line parted.",
        "It struck the deck.",
    ]


def test_split_on_blank_line_without_full_stop():
    assert split_sentences("Case one\n\nThe line parted.") == ["Case one", "The line parted."]


def test_split_after_an_initial_before_a_common_sentence_opener():
    text = (
        "The fire started in hold B. The crew fought it. "
        "Tank C. It was empty. The master, J. A. Testperson, and M.V. Example agreed."
    )

    assert split_sentences(text) == [
        "The fire started in hold B.",
        "The crew fought it.",
        "Tank C.",
        "It was empty.",
        "The master, J. A. Testperson, and M.V. Example agreed.",
    ]


def test_initial_before_other_capitalised_words_does_not_split():
    # Known limit: "hold B. Crew members..." cannot be told apart from "J. Testperson".
    assert split_sentences("The fire started in hold B. Crew members fought it.") == [
        "The fire started in hold B. Crew members fought it."
    ]


def test_split_keeps_place_rank_and_reference_abbreviations():
    text = (
        "The vessel arrived off St. Helena at 0930. "
        "The 2nd Off. checked the gas test. "
        "Ref. 7 gives the tank plan, see Sec. 2.4 and Vol. 2. "
        "It cites ref. 8 and vol. 3 as well. "
        "The crew left."
    )

    assert split_sentences(text) == [
        "The vessel arrived off St. Helena at 0930.",
        "The 2nd Off. checked the gas test.",
        "Ref. 7 gives the tank plan, see Sec. 2.4 and Vol. 2.",
        "It cites ref. 8 and vol. 3 as well.",
        "The crew left.",
    ]


def test_lower_case_off_and_sec_still_end_a_sentence():
    text = (
        "The pump was switched off. The alarm sounded for 30 sec. The crew left the st. "
        "Nobody was hurt."
    )

    assert split_sentences(text) == [
        "The pump was switched off.",
        "The alarm sounded for 30 sec.",
        "The crew left the st.",
        "Nobody was hurt.",
    ]
