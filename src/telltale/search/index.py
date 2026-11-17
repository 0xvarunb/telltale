"""Hybrid search: BM25 keywords and embeddings, each keeping its top 40, fused by RRF."""

import json
import os
import re
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from telltale.models import Chunk
from telltale.search.embed import Embedder

TOP_N = 40
VECTORS_FILE = "vectors.npy"
IDS_FILE = "chunk_ids.json"
META_FILE = "index_meta.json"

_WORD = re.compile(r"\w+")


class SearchIndexError(Exception):
    """The saved index does not match the chunks or the embedder. Rebuild it."""


def tokenize(text: str) -> list[str]:
    """Lower-case words, used for both chunks and queries."""
    return _WORD.findall(text.lower())


def index_text(chunk: Chunk) -> str:
    return "\n".join([chunk.report_title, *chunk.passages])


def embedder_name(embedder: Embedder) -> str:
    """The embedding model's name (`model` attribute), else the embedder's class name."""
    return str(getattr(embedder, "model", None) or type(embedder).__name__)


def _write_atomic(path: Path, write) -> None:
    """Write through a temporary file in the same directory, then rename it into place."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("wb") as f:
            write(f)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def rrf(rankings: list[list[int]], k: int = 60) -> list[int]:
    """Reciprocal rank fusion. Ties keep the order items were first seen."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda item: -scores[item])


class HybridIndex:
    def __init__(self, chunks: list[Chunk], vectors: np.ndarray, embedder: Embedder):
        if len(vectors) != len(chunks):
            raise SearchIndexError(f"{len(vectors)} vectors for {len(chunks)} chunks")
        self._chunks = chunks
        self._vectors = vectors
        self._embedder = embedder
        corpus = [tokenize(index_text(c)) for c in chunks]
        self._bm25 = BM25Okapi(corpus) if any(corpus) else None

    @classmethod
    def build(cls, chunks: list[Chunk], embedder: Embedder) -> "HybridIndex":
        if not chunks:
            return cls([], np.zeros((0, 0), dtype=np.float32), embedder)
        vectors = np.asarray(embedder.embed([index_text(c) for c in chunks]), dtype=np.float32)
        return cls(list(chunks), vectors, embedder)

    def save(self, dir: Path) -> None:
        """Write vectors, then `index_meta.json` (model name and dimension), then the chunk ids.

        Each file is replaced whole, and the ids go last, so a save cut short leaves files
        that `load` rejects rather than vectors paired with the wrong chunks.
        """
        dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "model": embedder_name(self._embedder),
            "dim": int(self._vectors.shape[1]) if self._vectors.ndim == 2 else 0,
        }
        _write_atomic(dir / VECTORS_FILE, lambda f: np.save(f, self._vectors))
        _write_atomic(dir / META_FILE, lambda f: f.write(json.dumps(meta).encode()))
        ids = json.dumps([c.id for c in self._chunks]).encode()
        _write_atomic(dir / IDS_FILE, lambda f: f.write(ids))

    @classmethod
    def load(cls, dir: Path, chunks: list[Chunk], embedder: Embedder) -> "HybridIndex":
        """Reload saved vectors for `chunks`. The saved ids and the chunks must match exactly,
        and the index must have been built with the same embedding model."""
        if not all((dir / name).exists() for name in (VECTORS_FILE, IDS_FILE, META_FILE)):
            raise SearchIndexError(f"no saved search index in {dir}; build it first")
        try:
            meta = json.loads((dir / META_FILE).read_text())
            ids = json.loads((dir / IDS_FILE).read_text())
            vectors = np.load(dir / VECTORS_FILE, allow_pickle=False)
        except (OSError, ValueError, EOFError) as error:
            raise SearchIndexError(
                f"saved index in {dir} is unreadable ({error}); rebuild it"
            ) from error
        if not isinstance(meta, dict) or not isinstance(ids, list):
            raise SearchIndexError(f"saved index in {dir} is malformed; rebuild it")
        if meta.get("model") != embedder_name(embedder):
            raise SearchIndexError(
                f"saved index was built with {meta.get('model')!r}, not "
                f"{embedder_name(embedder)!r}; rebuild it"
            )
        if vectors.ndim == 2 and len(vectors) and meta.get("dim") != vectors.shape[1]:
            raise SearchIndexError(
                f"saved index metadata says {meta.get('dim')} dimensions, the vectors have "
                f"{vectors.shape[1]}; rebuild it"
            )
        dim = getattr(embedder, "dim", None)
        if dim is not None and len(vectors) and meta.get("dim") != dim:
            raise SearchIndexError(
                f"saved index has {meta.get('dim')} dimensions, the embedder {dim}; rebuild it"
            )
        if vectors.ndim != 2 or len(vectors) != len(ids):
            raise SearchIndexError(
                f"saved index has {len(vectors)} vector rows for {len(ids)} chunk ids; rebuild it"
            )
        if len(set(ids)) != len(ids):
            raise SearchIndexError("saved index repeats a chunk id; rebuild it")

        by_id = {c.id: c for c in chunks}
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            raise SearchIndexError(
                f"saved index names {len(unknown)} chunk(s) not in the store "
                f"(first ids {unknown[:5]}); rebuild it"
            )
        unindexed = sorted(by_id.keys() - set(ids))
        if unindexed:
            raise SearchIndexError(
                f"{len(unindexed)} chunk(s) in the store are not in the saved index "
                f"(first ids {unindexed[:5]}); rebuild it"
            )
        return cls([by_id[i] for i in ids], vectors.astype(np.float32, copy=False), embedder)

    def search(self, query: str, k: int = 8) -> list[Chunk]:
        """Up to `k` chunks, best first. A query with no words returns nothing."""
        tokens = tokenize(query)
        if not tokens or not self._chunks or k <= 0:
            return []
        fused = rrf([self._keyword_ranking(tokens), self._vector_ranking(query)])
        return [self._chunks[i] for i in fused[:k]]

    def _keyword_ranking(self, tokens: list[str]) -> list[int]:
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(tokens)
        order = np.argsort(-scores, kind="stable")[:TOP_N]
        return [int(i) for i in order if scores[i] > 0]

    def _vector_ranking(self, query: str) -> list[int]:
        q = np.asarray(self._embedder.embed([query]), dtype=np.float32)[0]
        if q.shape[0] != self._vectors.shape[1]:
            raise SearchIndexError(
                f"query vector has {q.shape[0]} dimensions, the index {self._vectors.shape[1]}; "
                "rebuild the index with this embedding model"
            )
        scores = self._vectors @ q
        return [int(i) for i in np.argsort(-scores, kind="stable")[:TOP_N]]
