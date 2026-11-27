"""Hybrid search: BM25 keywords and embeddings, each keeping its top 40, fused by RRF."""

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from telltale.models import Chunk
from telltale.search.embed import Embedder

TOP_N = 40
VECTORS_FILE = "vectors.npy"
IDS_FILE = "chunk_ids.json"
META_FILE = "index_meta.json"
CACHE_FILE = "embed_cache.npz"

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


def text_hash(text: str) -> str:
    """The key a saved embedding is kept under: sha256 of the exact index text."""
    return hashlib.sha256(text.encode()).hexdigest()


def _groups(n: int, kind: str = "") -> str:
    return f"{n:,} {kind}page group" + ("" if n == 1 else "s")


def _read_cache(path: Path, model: str, dim: int | None) -> tuple[dict[str, np.ndarray], str]:
    """Saved vectors by text hash, or an empty dict and the reason none can be reused.

    Never raises for a bad file: a missing, unreadable, malformed or other-model cache only
    means everything is embedded again.
    """
    if not path.exists():
        return {}, "No saved embeddings to reuse"
    unreadable = {}, f"Saved embeddings in {path} could not be read"
    try:
        saved = np.load(path, allow_pickle=False)
    except (OSError, ValueError, EOFError, zipfile.BadZipFile):
        return unreadable
    if not isinstance(saved, np.lib.npyio.NpzFile):  # a plain .npy array has no named arrays
        return unreadable
    try:
        with saved:
            saved_model, hashes, vectors = saved["model"], saved["hashes"], saved["vectors"]
    except (OSError, ValueError, EOFError, KeyError, zipfile.BadZipFile):
        return unreadable
    if saved_model.ndim != 0 or saved_model.dtype.kind != "U":
        return unreadable
    if str(saved_model) != model:
        return {}, "Saved embeddings are from another model"
    well_formed = (
        hashes.ndim == 1
        and hashes.dtype.kind == "U"
        and vectors.ndim == 2
        and vectors.dtype.kind == "f"
        and len(hashes) == len(vectors)
        and (vectors.shape[1] > 0 or len(vectors) == 0)
        and (dim is None or len(vectors) == 0 or vectors.shape[1] == dim)
    )
    if not well_formed:
        return unreadable
    vectors = vectors.astype(np.float32, copy=False)
    return {str(h): vectors[i] for i, h in enumerate(hashes)}, ""


def _write_cache(path: Path, model: str, hashes: list[str], vectors: np.ndarray) -> None:
    """One file, replaced whole, so a save cut short never pairs one run's hashes with another
    run's vectors. Strings are fixed-width unicode so it loads with `allow_pickle=False`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(
        path,
        lambda f: np.savez(
            f,
            model=np.array(model, dtype=str),
            hashes=np.array(hashes, dtype=str),
            vectors=vectors.astype(np.float32, copy=False),
        ),
    )


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
    def build(
        cls, chunks: list[Chunk], embedder: Embedder, cache_dir: Path | None = None
    ) -> "HybridIndex":
        """Embed every chunk and index it. With `cache_dir`, reuse the vector saved there for any
        chunk whose exact index text was embedded before by the same model, embed only the rest,
        then save the vectors of the current chunks (and no others) for the next build."""
        if not chunks:
            return cls([], np.zeros((0, 0), dtype=np.float32), embedder)
        texts = [index_text(c) for c in chunks]
        if cache_dir is None:
            vectors = np.asarray(embedder.embed(texts), dtype=np.float32)
            return cls(list(chunks), vectors, embedder)

        model = embedder_name(embedder)
        hashes = [text_hash(t) for t in texts]
        cached, why_not = _read_cache(cache_dir / CACHE_FILE, model, getattr(embedder, "dim", None))
        if why_not:
            print(f"{why_not}; embedding all {_groups(len(chunks))}.", flush=True)
        else:
            new = sum(h not in cached for h in hashes)
            rest = f"embedding {_groups(new, 'new ')}" if new else "no new page groups to embed"
            print(f"Reused {len(hashes) - new:,} saved embeddings; {rest}.", flush=True)
        found = cls._embed_missing(embedder, texts, hashes, cached)
        if found is None:  # same model name, but its vectors no longer have the saved width
            print(
                f"Saved embeddings have another width; embedding all {_groups(len(chunks))}.",
                flush=True,
            )
            found = cls._embed_missing(embedder, texts, hashes, {})
        assert found is not None  # nothing cached, so the widths cannot disagree
        # One row per distinct text, current chunks only, so the cache never grows with texts
        # that have left the store.
        _write_cache(cache_dir / CACHE_FILE, model, list(found), np.stack(list(found.values())))
        return cls(list(chunks), np.stack([found[h] for h in hashes]), embedder)

    @staticmethod
    def _embed_missing(
        embedder: Embedder, texts: list[str], hashes: list[str], cached: dict[str, np.ndarray]
    ) -> dict[str, np.ndarray] | None:
        """A vector for every distinct text, by hash, in chunk order: the cached one where there
        is one, the rest from one `embed` call. None if the new and saved widths differ."""
        missing: dict[str, str] = {}
        for h, t in zip(hashes, texts):
            if h not in cached:
                missing.setdefault(h, t)
        fresh: dict[str, np.ndarray] = {}
        if missing:
            rows = np.asarray(embedder.embed(list(missing.values())), dtype=np.float32)
            if rows.ndim != 2 or len(rows) != len(missing):
                raise SearchIndexError(
                    f"the embedder returned {len(rows)} rows for {len(missing)} texts"
                )
            if cached and next(iter(cached.values())).shape[0] != rows.shape[1]:
                return None  # every saved row has one width; `_read_cache` checked it
            fresh = dict(zip(missing, rows))
        return {h: cached[h] if h in cached else fresh[h] for h in dict.fromkeys(hashes)}

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
