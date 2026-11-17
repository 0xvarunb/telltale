from pathlib import Path

from telltale import cli
from telltale.departments import BRIDGE, CATERING, DECK, ENGINE, departments
from telltale.ingest.store import Store
from telltale.models import ReportMeta


def placed(title: str) -> dict[str, list[str]]:
    return {p.department: p.matched for p in departments(title)}


def test_one_department_from_title_words():
    assert placed("Fire in engine room of general cargo vessel Test Ship") == {ENGINE: ["engine room"]}
    assert placed("Parting of mooring line on bulk carrier Test Ship") == {DECK: ["mooring"]}
    assert placed("Grounding of general cargo vessel Test Ship") == {BRIDGE: ["grounding"]}
    assert placed("Fire in main galley of passenger cruise ship Test Ship") == {CATERING: ["galley"]}


def test_a_report_can_belong_to_two_departments():
    assert placed("Engine failure and subsequent grounding of tanker Test Ship") == {
        BRIDGE: ["grounding"],
        ENGINE: ["engine"],
    }


def test_ship_type_words_do_not_place_a_report():
    # "general cargo vessel" and "passenger ferry" say what the ship is, not where it happened.
    assert placed("Capsize of general cargo vessel Test Ship") == {}
    assert placed("Flooding of ro-ro passenger ferry Test Ship") == {}
    assert placed("Diving incident involving self-propelled crane Test Ship") == {}


def test_command_counts_and_writes_csv(tmp_path: Path, capsys):
    def report(slug: str, title: str) -> ReportMeta:
        return ReportMeta(slug, title, f"https://www.gov.uk/maib-reports/{slug}",
                          "2026-01-01", None, [], "investigation-report", None)

    with Store(tmp_path / "telltale.sqlite") as store:
        store.upsert_report(report("a", "Fire in engine room of Test Ship"))
        store.upsert_report(report("b", "Parting of mooring line on Test Ship"))
        store.upsert_report(report("c", "Capsize of Test Ship"))

    cli.sort_by_department(tmp_path, show_list=True)
    out = capsys.readouterr().out

    assert "3 reports, sorted from the words in their titles:" in out
    assert f"  {ENGINE}: 1" in out
    assert f"  {DECK}: 1" in out
    assert "  Not sorted (title too general): 1" in out
    assert "  - Capsize of Test Ship" in out
    csv_text = (tmp_path / "departments.csv").read_text(encoding="utf-8")
    assert "Fire in engine room of Test Ship" in csv_text
    assert "Engine: engine room" in csv_text
