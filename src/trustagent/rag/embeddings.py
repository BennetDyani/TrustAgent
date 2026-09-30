"""One embedding model for ingestion AND querying (bug #6, ADR-004).

Model and dimension come from config. Documents and queries use Gemini's
retrieval task types. Every vector is L2-normalised here: Google's docs (and
our probe: norm ~0.59) show that vectors below 3072 dimensions are not
normalised, and we want cosine and dot product to agree.
"""

import math
from functools import cache
from typing import Protocol

from langchain_core.rate_limiters import InMemoryRateLimiter

from trustagent.config import get_settings

BATCH = 20


@cache
def _shared_limiter(per_minute: float) -> InMemoryRateLimiter:
    """One limiter for every embedder in the process: ingestion and queries share the same quota."""
    return InMemoryRateLimiter(requests_per_second=per_minute / 60)


class Embedder(Protocol):
    model: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def l2_normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


class GeminiEmbedder:
    def __init__(self) -> None:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        from trustagent.llm.factory import provider_api_key

        s = get_settings()
        self.model, self.dim = s.embedding_model, s.embedding_dim
        self._client = GoogleGenerativeAIEmbeddings(model=self.model, google_api_key=provider_api_key(s, "gemini"))
        # Quota counts every text, so pace per text, not per HTTP call (ADR-050).
        self._limiter = _shared_limiter(s.embedding_requests_per_minute)

    def _pace(self, n: int) -> None:
        for _ in range(n):
            self._limiter.acquire()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            batch = texts[i : i + BATCH]
            self._pace(len(batch))
            vectors += self._client.embed_documents(
                batch, task_type="RETRIEVAL_DOCUMENT", output_dimensionality=self.dim
            )
        return [self._check(l2_normalise(v)) for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        self._pace(1)
        vector = self._client.embed_query(text, task_type="RETRIEVAL_QUERY", output_dimensionality=self.dim)
        return self._check(l2_normalise(vector))

    def _check(self, vector: list[float]) -> list[float]:
        if len(vector) != self.dim:
            raise ValueError(f"{self.model} returned {len(vector)} dimensions; EMBEDDING_DIM is {self.dim}.")
        return vector
