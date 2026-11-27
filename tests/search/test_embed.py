import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from telltale.search import EmbedderUnavailable, FastEmbedder
from telltale.search.embed import BATCH_SIZE, PROGRESS_EVERY


def test_model_loads_lazily_into_cache_dir(monkeypatch, tmp_path: Path):
    loads: list[dict] = []

    class FakeTextEmbedding:
        def __init__(self, model_name, cache_dir=None):
            loads.append({"model_name": model_name, "cache_dir": cache_dir})

        def embed(self, texts, **kwargs):
            return (np.array([3.0, 4.0]) for _ in texts)

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=FakeTextEmbedding))
    embedder = FastEmbedder(cache_dir=tmp_path / "models")
    assert loads == []

    vectors = embedder.embed(["one", "two"])

    assert loads == [
        {"model_name": "BAAI/bge-small-en-v1.5", "cache_dir": str(tmp_path / "models")}
    ]
    assert np.allclose(vectors, [[0.6, 0.8], [0.6, 0.8]])


def test_failed_model_load_raises_embedder_unavailable(monkeypatch):
    class OfflineTextEmbedding:
        def __init__(self, model_name, cache_dir=None):
            raise OSError("could not reach the model host")

    monkeypatch.setitem(
        sys.modules, "fastembed", SimpleNamespace(TextEmbedding=OfflineTextEmbedding)
    )

    with pytest.raises(EmbedderUnavailable, match="could not be loaded"):
        FastEmbedder().embed(["one"])


def test_large_embed_reports_progress(monkeypatch, capsys):
    class FakeTextEmbedding:
        def __init__(self, model_name, cache_dir=None):
            pass

        def embed(self, texts, **kwargs):
            return (np.array([1.0, 0.0]) for _ in texts)

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=FakeTextEmbedding))

    FastEmbedder().embed(["x"] * 1200)
    err = capsys.readouterr().err
    assert "Embedded 500 of 1,200 page groups." in err
    assert "Embedded 1,000 of 1,200 page groups." in err

    FastEmbedder().embed(["x"] * 3)
    assert capsys.readouterr().err == ""


def test_every_batch_is_embedded_in_small_fastembed_batches(monkeypatch):
    calls: list[tuple[int, dict]] = []

    class FakeTextEmbedding:
        def __init__(self, model_name, cache_dir=None):
            pass

        def embed(self, texts, **kwargs):
            calls.append((len(texts), kwargs))
            return (np.array([1.0, 0.0]) for _ in texts)

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=FakeTextEmbedding))
    embedder = FastEmbedder()

    embedder.embed(["page group"] * PROGRESS_EVERY)
    embedder.embed(["page group"] * (PROGRESS_EVERY - 1))  # an incremental re-run
    embedder.embed(["a single query"])

    small = {"batch_size": 32}  # no `parallel`: never worker processes
    assert BATCH_SIZE == 32
    assert calls == [(PROGRESS_EVERY, small), (PROGRESS_EVERY - 1, small), (1, small)]
