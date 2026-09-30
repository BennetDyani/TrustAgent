"""RAG test fixtures: a deterministic fake embedder (unit tests only) and an ingested test database."""

import hashlib
import re

import pytest

from tests.graph.conftest import clean_db, upload  # noqa: F401  (re-exported fixtures)
from trustagent.config import get_settings
from trustagent.rag.embeddings import l2_normalise
from trustagent.rag.ingest import ingest_all


class FakeEmbedder:
    """Hashed bag of words: texts sharing words get similar vectors. md5, not hash(): hash() is salted per process."""

    model = "fake-bag-of-words"

    def __init__(self) -> None:
        self.dim = get_settings().embedding_dim
        self.calls = 0

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        return l2_normalise(v)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return self._vec(text)


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def ingested(clean_db, embedder):  # noqa: F811
    """Test database with every document in the manifest ingested."""
    with clean_db() as s, s.begin():
        counts = ingest_all(s, embedder)
    return counts
