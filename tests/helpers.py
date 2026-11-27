"""Test helpers shared across test modules."""

import re
import zlib

import numpy as np


class HashEmbedder:
    """Offline, deterministic stand-in for the real model: each word adds 1 to one of 16 slots."""

    dim = 16

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"\w+", text.lower()):
                vectors[row, zlib.crc32(word.encode()) % self.dim] += 1.0
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.where(norms == 0, 1, norms)


class CountingEmbedder(HashEmbedder):
    """HashEmbedder that records the texts of every `embed` call, one list per call."""

    def __init__(self, model: str = "test/hash-model"):
        self.model = model
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        return super().embed(texts)
