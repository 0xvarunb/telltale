import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from helpers import HashEmbedder
from telltale.models import Chunk
from telltale.search import HybridIndex, SearchIndexError, rrf

PDF_URL = "https://assets.publishing.service.gov.uk/media/test/report.pdf"


def chunk(id_: int, title: str, passages: list[str], page: int = 1) -> Chunk:
    return Chunk(
        id=id_,
        report_slug=f"test-report-{id_}",
        report_title=title,
        url=PDF_URL,
        page=page,
        passages=passages,
    )


@pytest.fixture
def chunks() -> list[Chunk]:
    return [
        chunk(11, "Fall on a gangway", [
            "A crew member was entering the accommodation at night.",
            "The gangway net was missing.",
        ]),
        chunk(12, "Enclosed space fatality", [
            "The bosun went into the ballast tank without a gas test.",
            "The ballast tank atmosphere was deficient in oxygen.",
        ]),
        chunk(15, "Mooring line failure", [
            "A mooring line parted during berthing.",
            "The snap-back zone was not marked on deck.",
        ]),
    ]


@pytest.fixture
def index(chunks) -> HybridIndex:
    return HybridIndex.build(chunks, HashEmbedder())


def ids(results: list[Chunk]) -> list[int]:
    return [c.id for c in results]


def test_rrf_rewards_agreement():
    assert rrf([[1, 2, 3], [3, 1, 2]])[0] == 1


def test_keyword_match_ranks_first(index):
    assert ids(index.search("entering a ballast tank"))[0] == 12


def test_save_load_same_results(index, chunks, tmp_path: Path):
    queries = ["entering a ballast tank", "mooring line", "gangway at night", "oxygen"]
    before = [ids(index.search(q)) for q in queries]

    index.save(tmp_path / "index")
    assert (tmp_path / "index" / "vectors.npy").exists()
    assert (tmp_path / "index" / "chunk_ids.json").exists()
    loaded = HybridIndex.load(tmp_path / "index", chunks, HashEmbedder())

    assert [ids(loaded.search(q)) for q in queries] == before


def test_returns_at_most_k(index):
    assert len(index.search("the ballast tank and the mooring line on deck", k=2)) == 2


def test_each_ranker_keeps_its_top_40():
    many = [chunk(i, f"Report {i}", [f"Passage number {i}."]) for i in range(1, 51)]
    index = HybridIndex.build(many, HashEmbedder())
    # No keyword matches, so only the embedding ranker contributes, capped at 40.
    assert len(index.search("zzzz", k=50)) == 40


def test_empty_query_returns_nothing(index):
    assert index.search("") == []
    assert index.search("   ") == []
    assert index.search("?!") == []


def test_empty_chunk_list(tmp_path: Path):
    index = HybridIndex.build([], HashEmbedder())
    assert index.search("ballast tank") == []
    index.save(tmp_path / "index")
    assert HybridIndex.load(tmp_path / "index", [], HashEmbedder()).search("ballast tank") == []


def test_load_rejects_ids_missing_from_chunks(index, chunks, tmp_path: Path):
    index.save(tmp_path / "index")
    with pytest.raises(SearchIndexError, match="not in the store"):
        HybridIndex.load(tmp_path / "index", chunks[:2], HashEmbedder())


def test_load_rejects_chunks_missing_from_index(index, chunks, tmp_path: Path):
    index.save(tmp_path / "index")
    extra = chunk(16, "Galley fire", ["Oil in a deep fat fryer caught fire."])
    with pytest.raises(SearchIndexError, match="not in the saved index"):
        HybridIndex.load(tmp_path / "index", [*chunks, extra], HashEmbedder())


def test_load_rejects_row_count_mismatch(index, chunks, tmp_path: Path):
    index.save(tmp_path / "index")
    vectors = np.load(tmp_path / "index" / "vectors.npy")
    np.save(tmp_path / "index" / "vectors.npy", vectors[:2])
    with pytest.raises(SearchIndexError, match="rows"):
        HybridIndex.load(tmp_path / "index", chunks, HashEmbedder())


def test_load_rejects_missing_files(chunks, tmp_path: Path):
    with pytest.raises(SearchIndexError, match="no saved search index"):
        HybridIndex.load(tmp_path / "nowhere", chunks, HashEmbedder())


def test_importing_search_loads_no_model():
    code = (
        "import sys, telltale.search as s; s.FastEmbedder(); "
        "assert 'fastembed' not in sys.modules, 'fastembed imported'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_save_writes_meta_and_leaves_no_temp_files(index, tmp_path: Path):
    index.save(tmp_path / "index")

    meta = json.loads((tmp_path / "index" / "index_meta.json").read_text())
    assert meta == {"model": "HashEmbedder", "dim": 16}
    assert sorted(p.name for p in (tmp_path / "index").iterdir()) == [
        "chunk_ids.json",
        "index_meta.json",
        "vectors.npy",
    ]


def test_save_replaces_an_older_index(index, chunks, tmp_path: Path):
    HybridIndex.build(chunks[:1], HashEmbedder()).save(tmp_path / "index")
    index.save(tmp_path / "index")

    assert len(HybridIndex.load(tmp_path / "index", chunks, HashEmbedder()).search("oxygen")) > 0


class OtherEmbedder(HashEmbedder):
    model = "test/other-model"


def test_load_rejects_other_embedding_model(index, chunks, tmp_path: Path):
    index.save(tmp_path / "index")

    with pytest.raises(SearchIndexError, match="other-model"):
        HybridIndex.load(tmp_path / "index", chunks, OtherEmbedder())


def test_load_rejects_other_dimension(index, chunks, tmp_path: Path):
    index.save(tmp_path / "index")

    class WiderEmbedder(HashEmbedder):
        model = "HashEmbedder"  # same name, other size
        dim = 32

    with pytest.raises(SearchIndexError, match="dimensions"):
        HybridIndex.load(tmp_path / "index", chunks, WiderEmbedder())


def test_load_rejects_missing_meta(index, chunks, tmp_path: Path):
    index.save(tmp_path / "index")
    (tmp_path / "index" / "index_meta.json").unlink()

    with pytest.raises(SearchIndexError, match="no saved search index"):
        HybridIndex.load(tmp_path / "index", chunks, HashEmbedder())


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("chunk_ids.json", b"[11, 12,"),
        ("index_meta.json", b"not json"),
        ("vectors.npy", b"not a numpy file"),
        ("vectors.npy", b""),
    ],
)
def test_load_wraps_corrupt_files(index, chunks, tmp_path: Path, name, content):
    index.save(tmp_path / "index")
    (tmp_path / "index" / name).write_bytes(content)

    with pytest.raises(SearchIndexError, match="rebuild"):
        HybridIndex.load(tmp_path / "index", chunks, HashEmbedder())
