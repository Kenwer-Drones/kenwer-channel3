"""Deterministic local embedding helper for semantic candidate retrieval.

Uses a fixed hashing / bag-of-tokens projection into 384 dims so pgvector
cosine distance works without an external embedding API. This is for
**candidate retrieval ranking only** - never for agreement/contradiction
decisions or authorization.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable

EMBEDDING_DIM = 384
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 2]


def embed_text(text: str, *, dim: int = EMBEDDING_DIM) -> list[float]:
    """Project text into a unit vector of length ``dim`` (deterministic)."""
    tokens = tokenize(text)
    if not tokens:
        return [0.0] * dim
    vec = [0.0] * dim
    for tok in tokens:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        # Two 32-bit indices + signs from the digest
        for i in range(0, 16, 4):
            idx = int.from_bytes(digest[i : i + 2], "little") % dim
            sign = 1.0 if digest[i + 2] % 2 == 0 else -1.0
            weight = 1.0 + (digest[i + 3] / 255.0)
            vec[idx] += sign * weight
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine_similarity(a: Iterable[float], b: Iterable[float]) -> float:
    a_list = list(a)
    b_list = list(b)
    if not a_list or not b_list or len(a_list) != len(b_list):
        return 0.0
    if all(v == 0.0 for v in a_list) or all(v == 0.0 for v in b_list):
        return 0.0
    return sum(x * y for x, y in zip(a_list, b_list, strict=True))
