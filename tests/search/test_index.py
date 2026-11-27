import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from helpers import CountingEmbedder, HashEmbedder
from telltale.models import Chunk
from telltale.search import HybridIndex, SearchIndexError, rrf
from telltale.search.index import CACHE_FILE, index_text

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


# Reusing saved embeddings: a page group whose exact index text was already embedded by the same
# model keeps that vector; anything else is embedded again.


def saved_vectors(index: HybridIndex, dir: Path) -> dict[int, np.ndarray]:
    """The index's vector for each chunk id, read back from a save."""
    index.save(dir)
    ids = json.loads((dir / "chunk_ids.json").read_text())
    vectors = np.load(dir / "vectors.npy", allow_pickle=False)
    return dict(zip(ids, vectors))


def text_hash(c: Chunk) -> str:
    return hashlib.sha256(index_text(c).encode()).hexdigest()


NEW = chunk(16, "Lifeboat release", ["The on-load hook opened while the boat was hoisted."])


def test_build_reuses_saved_embeddings_for_unchanged_text(chunks, tmp_path: Path, capsys):
    cache = tmp_path / "index"
    first = CountingEmbedder()
    before = saved_vectors(HybridIndex.build(chunks, first, cache_dir=cache), tmp_path / "a")
    assert first.calls == [[index_text(c) for c in chunks]]
    assert "No saved embeddings to reuse; embedding all 3 page groups." in capsys.readouterr().out
    assert (cache / CACHE_FILE).exists()

    second = CountingEmbedder()
    after = saved_vectors(
        HybridIndex.build([*chunks, NEW], second, cache_dir=cache), tmp_path / "b"
    )

    assert second.calls == [[index_text(NEW)]]
    for c in chunks:
        assert np.array_equal(after[c.id], before[c.id])
    assert "Reused 3 saved embeddings; embedding 1 new page group." in capsys.readouterr().out


def test_a_changed_title_is_embedded_again(chunks, tmp_path: Path):
    cache = tmp_path / "index"
    old = saved_vectors(HybridIndex.build(chunks, CountingEmbedder(), cache_dir=cache), tmp_path / "a")
    retitled = replace(chunks[0], report_title="Fall from an unlit gangway at night")

    second = CountingEmbedder()
    new = saved_vectors(
        HybridIndex.build([retitled, *chunks[1:]], second, cache_dir=cache), tmp_path / "b"
    )

    assert second.calls == [[index_text(retitled)]]
    expected = HashEmbedder().embed([index_text(retitled)])[0]
    assert np.array_equal(new[retitled.id], expected)
    assert not np.array_equal(new[retitled.id], old[retitled.id])


def test_cache_from_another_model_is_ignored(chunks, tmp_path: Path, capsys):
    cache = tmp_path / "index"
    HybridIndex.build(chunks, CountingEmbedder("test/model-a"), cache_dir=cache)
    capsys.readouterr()

    other = CountingEmbedder("test/model-b")
    HybridIndex.build(chunks, other, cache_dir=cache)

    assert other.calls == [[index_text(c) for c in chunks]]
    out = capsys.readouterr().out
    assert "Saved embeddings are from another model; embedding all 3 page groups." in out
    with np.load(cache / CACHE_FILE, allow_pickle=False) as saved:
        assert str(saved["model"]) == "test/model-b"


def _garbage(path: Path, hashes: list[str]) -> None:
    path.write_bytes(b"not a saved embedding cache")


def _lengths_differ(path: Path, hashes: list[str]) -> None:
    vectors = np.ones((len(hashes) - 1, HashEmbedder.dim), dtype=np.float32)
    np.savez(path, model=np.array("test/hash-model"), hashes=np.array(hashes), vectors=vectors)


def _missing_vectors(path: Path, hashes: list[str]) -> None:
    np.savez(path, model=np.array("test/hash-model"), hashes=np.array(hashes))


def _other_width(path: Path, hashes: list[str]) -> None:
    vectors = np.ones((len(hashes), HashEmbedder.dim + 1), dtype=np.float32)
    np.savez(path, model=np.array("test/hash-model"), hashes=np.array(hashes), vectors=vectors)


def _one_dimensional(path: Path, hashes: list[str]) -> None:
    vectors = np.ones(len(hashes), dtype=np.float32)
    np.savez(path, model=np.array("test/hash-model"), hashes=np.array(hashes), vectors=vectors)


def _plain_npy(path: Path, hashes: list[str]) -> None:
    with path.open("wb") as f:  # a plain .npy array under the cache's name, not an .npz
        np.save(f, np.ones((len(hashes), HashEmbedder.dim), dtype=np.float32))


def _pickled_hashes(path: Path, hashes: list[str]) -> None:
    vectors = np.ones((len(hashes), HashEmbedder.dim), dtype=np.float32)
    np.savez(
        path,
        model=np.array("test/hash-model"),
        hashes=np.array(hashes, dtype=object),
        vectors=vectors,
    )


@pytest.mark.parametrize(
    "write",
    [
        _garbage,
        _lengths_differ,
        _missing_vectors,
        _other_width,
        _one_dimensional,
        _pickled_hashes,
        _plain_npy,
    ],
)
def test_unreadable_or_mismatched_cache_embeds_everything(chunks, tmp_path: Path, capsys, write):
    cache = tmp_path / "index"
    cache.mkdir()
    write(cache / CACHE_FILE, [text_hash(c) for c in chunks])

    embedder = CountingEmbedder()
    index = HybridIndex.build(chunks, embedder, cache_dir=cache)

    assert embedder.calls == [[index_text(c) for c in chunks]]
    assert "embedding all 3 page groups." in capsys.readouterr().out
    assert ids(index.search("entering a ballast tank"))[0] == 12
    with np.load(cache / CACHE_FILE, allow_pickle=False) as saved:  # replaced by a good one
        assert list(saved["hashes"]) == [text_hash(c) for c in chunks]


def test_reused_build_matches_a_fresh_build(chunks, tmp_path: Path):
    cache = tmp_path / "index"
    HybridIndex.build(chunks, CountingEmbedder(), cache_dir=cache)
    reused = HybridIndex.build([*chunks, NEW], CountingEmbedder(), cache_dir=cache)
    fresh = HybridIndex.build([*chunks, NEW], CountingEmbedder())

    a, b = saved_vectors(reused, tmp_path / "a"), saved_vectors(fresh, tmp_path / "b")
    assert a.keys() == b.keys()
    for id_ in a:
        assert np.array_equal(a[id_], b[id_])
    for query in ["entering a ballast tank", "lifeboat hook", "mooring line", "oxygen"]:
        assert ids(reused.search(query)) == ids(fresh.search(query))


def test_cache_holds_only_current_chunks(chunks, tmp_path: Path):
    cache = tmp_path / "index"
    HybridIndex.build(chunks, CountingEmbedder(), cache_dir=cache)

    HybridIndex.build(chunks[:2], CountingEmbedder(), cache_dir=cache)

    with np.load(cache / CACHE_FILE, allow_pickle=False) as saved:
        hashes = list(saved["hashes"])
        assert saved["vectors"].shape == (2, HashEmbedder.dim)
    assert hashes == [text_hash(c) for c in chunks[:2]]
    assert text_hash(chunks[2]) not in hashes


def test_identical_texts_share_one_embedding(chunks, tmp_path: Path):
    twin = replace(chunks[0], id=99, report_slug="test-report-99")
    embedder = CountingEmbedder()

    vectors = saved_vectors(
        HybridIndex.build([*chunks, twin], embedder, cache_dir=tmp_path / "index"), tmp_path / "a"
    )

    assert embedder.calls == [[index_text(c) for c in chunks]]
    assert np.array_equal(vectors[twin.id], vectors[chunks[0].id])


def test_build_without_a_cache_dir_writes_no_cache(chunks, tmp_path: Path, capsys):
    index = HybridIndex.build(chunks, CountingEmbedder())
    index.save(tmp_path / "index")

    assert not (tmp_path / "index" / CACHE_FILE).exists()
    assert capsys.readouterr().out == ""
