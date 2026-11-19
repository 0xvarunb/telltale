from pathlib import Path

import pytest
from PIL import Image

from telltale import card, cli
from telltale.answer.brief import NO_ANSWER
from telltale.models import Answer, Citation, Sentence

PDF_A = "https://assets.publishing.service.gov.uk/media/test/report-alpha.pdf"
PDF_B = "https://assets.publishing.service.gov.uk/media/test/report-bravo.pdf"
ALPHA = Citation(report_title="Test report alpha", source=f"{PDF_A}#page=4", cited_text="Alpha.")
BRAVO = Citation(report_title="Test report bravo", source=f"{PDF_B}#page=9", cited_text="Bravo.")


def make_answer(sentences: list[Sentence], stop_reason: str = "end_turn") -> Answer:
    return Answer(
        question="We are about to enter a test tank on a test vessel. What has gone wrong?",
        sentences=sentences,
        chunks=[],
        input_tokens=7000,
        output_tokens=2000,
        cost_usd=0.0022,
        stop_reason=stop_reason,
    )


ANSWER = make_answer([
    Sentence(text="On the test vessel Alpha, the tank had not been ventilated.", citations=[ALPHA]),
    Sentence(text="A crew member collapsed at the bottom of the ladder.", citations=[]),
    Sentence(text="On the test vessel Bravo, the gas test was not repeated.", citations=[BRAVO, ALPHA]),
])


def test_view_numbers_match_the_command_line(capsys):
    cli.print_answer(ANSWER)
    printed = capsys.readouterr().out
    v = card.view(ANSWER)

    assert [(line.number, line.sources) for line in v.lines] == [(1, [1]), (2, []), (3, [2, 1])]
    assert "1. On the test vessel Alpha, the tank had not been ventilated. [1]" in printed
    assert "2. A crew member collapsed at the bottom of the ladder. [no source]" in printed
    assert "3. On the test vessel Bravo, the gas test was not repeated. [2][1]" in printed
    assert v.sources[2] == ("Test report bravo", 9, f"{PDF_B}#page=9")


def test_no_answer_sentence_is_plain_and_unnumbered():
    v = card.view(make_answer([Sentence(text=NO_ANSWER, citations=[])]))
    assert len(v.lines) == 1
    assert v.lines[0].plain and v.lines[0].number is None


def test_json_round_trip_keeps_everything_the_card_uses():
    again = card.from_json(card.to_json(ANSWER))
    assert again.question == ANSWER.question
    assert again.sentences == ANSWER.sentences
    assert (again.cost_usd, again.input_tokens, again.output_tokens, again.stop_reason) == (
        0.0022, 7000, 2000, "end_turn"
    )


@pytest.mark.parametrize(
    "spec, expected",
    [(None, None), ("", None), ("2", [2]), ("1-2", [1, 2]), ("3, 1-2", [3, 1, 2]), ("1,1", [1])],
)
def test_parse_selection(spec, expected):
    assert card.parse_selection(spec, 3) == expected


@pytest.mark.parametrize("spec", ["0", "4", "2-1", "a", "1-", "1;2"])
def test_parse_selection_refuses_bad_choices(spec):
    with pytest.raises(card.CardError):
        card.parse_selection(spec, 3)


def test_render_draws_a_phone_sized_png(tmp_path: Path):
    drawn = card.render(ANSWER, tmp_path / "card.png")

    with Image.open(drawn.path) as image:
        assert image.format == "PNG"
        assert image.size == (2160, 2700)
    assert drawn.shown == [1, 2, 3]
    assert drawn.left_out == 0


def test_render_with_a_selection_counts_what_was_left_out(tmp_path: Path):
    drawn = card.render(ANSWER, tmp_path / "card.png", [1, 3])
    assert drawn.shown == [1, 3]
    assert drawn.left_out == 1


def test_a_long_answer_keeps_whole_sentences_and_says_how_many_are_missing(tmp_path: Path):
    long = make_answer([
        Sentence(text=f"Sentence {n} about the test vessel, long enough to wrap over two or three "
                      "lines on the card so that not all of them can fit.", citations=[ALPHA])
        for n in range(1, 31)
    ])
    drawn = card.render(long, tmp_path / "card.png")
    assert 1 <= len(drawn.shown) < 30
    assert drawn.shown == list(range(1, len(drawn.shown) + 1))
    assert drawn.left_out == 30 - len(drawn.shown)


def test_an_answer_with_nothing_to_show_is_refused(tmp_path: Path):
    with pytest.raises(card.CardError):
        card.render(make_answer([]), tmp_path / "card.png")


def test_card_command_redraws_a_saved_answer(tmp_path: Path, capsys):
    saved = tmp_path / "answer.json"
    saved.write_text(card.to_json(ANSWER), encoding="utf-8")
    out = tmp_path / "out" / "card.png"

    assert cli.main(["card", str(saved), "--out", str(out), "--sentences", "1,3"]) == 0

    assert out.is_file()
    assert f"Card saved to {out}" in capsys.readouterr().out


def test_card_command_explains_a_bad_file_or_choice(tmp_path: Path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert cli.main(["card", str(bad), "--out", str(tmp_path / "c.png")]) == 1
    assert "not an answer saved by" in capsys.readouterr().err

    saved = tmp_path / "answer.json"
    saved.write_text(card.to_json(ANSWER), encoding="utf-8")
    assert cli.main(["card", str(saved), "--out", str(tmp_path / "c.png"), "--sentences", "9"]) == 1
    assert "outside the answer's sentences 1 to 3" in capsys.readouterr().err


def test_ask_saves_the_answer_and_draws_the_card(monkeypatch, tmp_path: Path, capsys):
    from telltale.settings import Settings

    monkeypatch.setattr(cli, "load_settings", lambda *a, **k: Settings(anthropic_api_key="test-key"))
    monkeypatch.setattr(cli, "load_index", lambda data_dir: object())
    monkeypatch.setattr(cli, "answer", lambda question, index, client, model: ANSWER)
    saved, out = tmp_path / "a.json", tmp_path / "a.png"

    code = cli.main(["ask", "Entering a test tank", "--save", str(saved), "--card", str(out)])

    assert code == 0
    assert card.from_json(saved.read_text(encoding="utf-8")).sentences == ANSWER.sentences
    assert out.is_file()


def test_ask_refuses_sentences_without_card(capsys):
    assert cli.main(["ask", "Entering a test tank", "--sentences", "1"]) == 1
    assert "--sentences only applies with --card" in capsys.readouterr().err


# A no-answer result lists the reports searched (titles and pages only).

NO_ANSWER_RESULT = make_answer([Sentence(text=NO_ANSWER, citations=[])])
SEARCHED = [
    # Retrieval order; report two appears twice and is listed once, at its first appearance.
    {"report_title": "Report one: fire in the engine room of a test ferry", "page": 12,
     "source": f"{PDF_A}#page=12"},
    {"report_title": "Report two: test cargo ship", "page": 3, "source": f"{PDF_B}#page=3"},
    {"report_title": "Report two: test cargo ship", "page": 7, "source": f"{PDF_B}#page=7"},
    {"report_title": "Report three: a GOV.UK page with no PDF", "page": None,
     "source": "https://www.gov.uk/maib-reports/test-three"},
    {"report_title": "Report four: test tug", "page": 5, "source": f"{PDF_A}#page=5"},
    {"report_title": "Report five: test fishing vessel", "page": 1, "source": f"{PDF_B}#page=1"},
    {"report_title": "Report six: test yacht", "page": 2, "source": f"{PDF_A}#page=2"},
]
TITLES = [
    "Report one: fire in the engine room of a test ferry",
    "Report two: test cargo ship",
    "Report three: a GOV.UK page with no PDF",
    "Report four: test tug",
    "Report five: test fishing vessel",
]
LABEL = "REPORTS SEARCHED (CLOSEST FIRST)"


def saved_json(result: Answer, searched: list[dict] | None) -> str:
    import json

    data = json.loads(card.to_json(result))
    if searched is None:
        del data["searched"]
    else:
        data["searched"] = searched
    return json.dumps(data)


def test_searched_from_json_keeps_first_appearance_of_each_report():
    reports = card.searched_from_json(saved_json(NO_ANSWER_RESULT, SEARCHED))
    assert [(r.title, r.page) for r in reports] == [
        (TITLES[0], 12), (TITLES[1], 3), (TITLES[2], None), (TITLES[3], 5), (TITLES[4], 1),
        ("Report six: test yacht", 2),
    ]


def test_searched_from_json_is_empty_for_an_older_file():
    assert card.searched_from_json(saved_json(NO_ANSWER_RESULT, None)) == []


def test_no_answer_card_lists_the_reports_searched(tmp_path: Path):
    text = saved_json(NO_ANSWER_RESULT, SEARCHED)
    drawn = card.render(card.from_json(text), tmp_path / "card.png",
                        searched=card.searched_from_json(text))

    joined = " ".join(drawn.text)
    assert NO_ANSWER in joined
    assert LABEL in drawn.text
    assert drawn.text.index(LABEL) > next(i for i, t in enumerate(drawn.text) if "no reliable" in t)
    positions = [joined.index(title) for title in TITLES]
    assert positions == sorted(positions)
    assert "Report six" not in joined
    assert "page None" not in joined
    assert f"{TITLES[0]}, page 12" in joined
    assert not any(t.endswith(", page") for t in drawn.text)  # "page N" is never split
    assert f"{TITLES[2]}," not in joined
    assert card.SEARCHED_CLOSING in joined
    assert joined.index(card.SEARCHED_CLOSING) > positions[-1]
    assert "more in the saved answer" not in joined
    with Image.open(drawn.path) as image:
        assert image.size == (2160, 2700)


def test_no_answer_card_from_live_chunks_lists_the_reports_searched(tmp_path: Path):
    from telltale.models import Chunk

    chunks = [
        Chunk(id=i, report_slug=f"slug-{i}", report_title=f"Live test report {i}",
              url=PDF_A, page=i + 1, passages=["x"])
        for i in range(3)
    ]
    result = make_answer([Sentence(text=NO_ANSWER, citations=[])])
    result.chunks = chunks
    drawn = card.render(result, tmp_path / "card.png")
    joined = " ".join(drawn.text)
    assert LABEL in drawn.text
    assert "Live test report 0, page 1" in joined
    assert "Live test report 2, page 3" in joined


def test_too_many_long_titles_show_fewer_reports_and_say_how_many_more(tmp_path: Path):
    long_title = " ".join(["Very long test report title that runs on and on"] * 16)
    searched = [{"report_title": f"{long_title} {n}", "page": n, "source": f"{PDF_A}#page={n}"}
                for n in range(1, 7)]
    text = saved_json(NO_ANSWER_RESULT, searched)
    drawn = card.render(card.from_json(text), tmp_path / "card.png",
                        searched=card.searched_from_json(text))
    joined = " ".join(drawn.text)
    shown = sum(1 for n in range(1, 6) if f"{long_title} {n}, page {n}" in joined)
    assert 1 <= shown < 5
    assert f"and {5 - shown} more in the saved answer." in joined
    assert card.SEARCHED_CLOSING in joined


def test_older_file_and_sourced_answer_draw_no_searched_block(tmp_path: Path):
    old = saved_json(NO_ANSWER_RESULT, None)
    drawn_old = card.render(card.from_json(old), tmp_path / "old.png",
                            searched=card.searched_from_json(old))
    assert LABEL not in drawn_old.text
    assert card.SEARCHED_CLOSING not in " ".join(drawn_old.text)
    assert " ".join(drawn_old.text).count("no reliable answer") == 1

    sourced = saved_json(ANSWER, SEARCHED)
    drawn_sourced = card.render(card.from_json(sourced), tmp_path / "sourced.png",
                                searched=card.searched_from_json(sourced))
    assert LABEL not in drawn_sourced.text
    assert card.SEARCHED_CLOSING not in " ".join(drawn_sourced.text)
    assert drawn_sourced.shown == [1, 2, 3]


def test_card_command_passes_the_saved_reports_searched(monkeypatch, tmp_path: Path):
    seen = {}
    real = card.render

    def spy(result, out, selection=None, searched=None):
        drawn = real(result, out, selection, searched=searched)
        seen["text"] = drawn.text
        return drawn

    monkeypatch.setattr(card, "render", spy)
    saved = tmp_path / "answer.json"
    saved.write_text(saved_json(NO_ANSWER_RESULT, SEARCHED), encoding="utf-8")

    assert cli.main(["card", str(saved), "--out", str(tmp_path / "c.png")]) == 0
    assert LABEL in seen["text"]


def test_a_report_with_scanned_pages_is_listed_once(tmp_path: Path):
    from telltale.ingest.store import OCR_NOTE

    plain, scanned = "Report one: test ferry", "Report one: test ferry" + OCR_NOTE
    rows = [
        (plain, 12, "slug-one"), (scanned, 49, "slug-one"), ("Report two: test tug", 3, "slug-two"),
        (scanned, 50, "slug-one"), ("Report three: test yacht", 4, "slug-three"),
        ("Report four: test barge", 5, "slug-four"), ("Report five: test trawler", 6, "slug-five"),
    ]
    for with_slug in (True, False):
        searched = [
            {"report_title": title, "page": page, "source": f"{PDF_A}#page={page}",
             **({"report_slug": slug} if with_slug else {})}
            for title, page, slug in rows
        ]
        text = saved_json(NO_ANSWER_RESULT, searched)
        reports = card.searched_from_json(text)
        assert [(r.title, r.page) for r in reports] == [
            (plain, 12), ("Report two: test tug", 3), ("Report three: test yacht", 4),
            ("Report four: test barge", 5), ("Report five: test trawler", 6),
        ], with_slug
        drawn = card.render(card.from_json(text), tmp_path / "card.png", searched=reports)
        joined = " ".join(drawn.text)
        assert "page 49" not in joined and OCR_NOTE.strip() not in joined
        assert "Report five: test trawler, page 6" in joined


def test_to_json_saves_the_report_slug_of_each_chunk_searched():
    import json

    from telltale.models import Chunk

    result = make_answer([Sentence(text=NO_ANSWER, citations=[])])
    result.chunks = [Chunk(id=1, report_slug="slug-x", report_title="Report x", url=PDF_A, page=2,
                           passages=["x"])]
    assert json.loads(card.to_json(result))["searched"] == [
        {"report_title": "Report x", "page": 2, "source": f"{PDF_A}#page=2", "report_slug": "slug-x"}
    ]


def test_two_reports_with_the_same_title_stay_apart_when_slugs_are_saved():
    searched = [
        {"report_title": "Same title", "page": 1, "source": f"{PDF_A}#page=1", "report_slug": "a"},
        {"report_title": "Same title", "page": 2, "source": f"{PDF_B}#page=2", "report_slug": "b"},
    ]
    reports = card.searched_from_json(saved_json(NO_ANSWER_RESULT, searched))
    assert [(r.title, r.page) for r in reports] == [("Same title", 1), ("Same title", 2)]
