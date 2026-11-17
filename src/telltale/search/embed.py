"""Text embeddings for search: the protocol the index needs and the local fastembed model."""

import sys
from pathlib import Path
from typing import Protocol

import numpy as np

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
PROGRESS_EVERY = 500


class EmbedderUnavailable(Exception):
    """The embedding model could not be loaded (for example, not cached and no internet)."""


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        """One row per text, L2-normalised, float32."""
        ...


def normalise(vectors: np.ndarray) -> np.ndarray:
    """L2-normalise each row as float32. An all-zero row stays zero."""
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


class FastEmbedder:
    """A small embedding model run locally on CPU through fastembed (ONNX, no torch).

    The model is loaded, and downloaded into `cache_dir` if not there, on the first `embed`
    call, so creating this object or importing `telltale.search` costs nothing. A failed load
    raises `EmbedderUnavailable`.

    Queries and passages are embedded the same way: fastembed 0.9.1 applies no prefix for this
    model either way, and BGE v1.5 is built to work without the query instruction.
    """

    def __init__(self, model: str = DEFAULT_MODEL, cache_dir: Path | None = None):
        self.model = model
        # fastembed's default cache is under the system temp dir, which a reboot can clear.
        self.cache_dir = cache_dir
        self._model = None

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        if self._model is None:
            try:
                from fastembed import TextEmbedding

                cache = str(self.cache_dir) if self.cache_dir is not None else None
                self._model = TextEmbedding(model_name=self.model, cache_dir=cache)
            except Exception as error:  # download, file and ONNX errors all mean the same here
                raise EmbedderUnavailable(
                    f"embedding model {self.model} could not be loaded: {error}"
                ) from error
        rows = []
        for row in self._model.embed(texts):
            rows.append(row)
            # A full ingest embeds thousands of page groups on CPU; show it is still working.
            if len(texts) >= PROGRESS_EVERY and len(rows) % PROGRESS_EVERY == 0:
                print(f"Embedded {len(rows):,} of {len(texts):,} page groups.", file=sys.stderr, flush=True)
        return normalise(np.stack(rows))
