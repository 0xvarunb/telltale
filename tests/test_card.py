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
