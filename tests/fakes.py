"""Test doubles shared by unit and integration tests (no model download, no network)."""

import hashlib
import math
import re
from collections.abc import AsyncIterator, Sequence

from app.llm import ChatMessage, LLMChunk, Usage

_WORD = re.compile(r"\w+")


class FakeEmbedder:
    """Deterministic bag-of-words embedder: texts sharing words get similar vectors.

    Implements app.embeddings.Embedder, so similarity searches can be tested
    against a real PostgreSQL without downloading a model.
    """

    def __init__(
        self, model_name: str = "fake-bow", dimension: int = 1024, max_input_tokens: int = 8192
    ) -> None:
        self.model_name = model_name
        self.dimension = dimension
        self.max_input_tokens = max_input_tokens
        self.calls: list[list[str]] = []

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for word in _WORD.findall(text.lower()):
            bucket = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big")
            vector[bucket % self.dimension] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def count_tokens(self, text: str) -> int:
        return len(_WORD.findall(text))


class FakeLLM:
    """Streams a canned answer word by word and records the messages it received."""

    def __init__(self, answer: str, model: str = "fake-llm") -> None:
        self.model = model
        self._answer = answer
        self.received: list[list[ChatMessage]] = []
        self.closed = False

    async def stream(
        self, messages: Sequence[ChatMessage], *, temperature: float
    ) -> AsyncIterator[LLMChunk]:
        self.received.append(list(messages))
        words = self._answer.split(" ")
        for i, word in enumerate(words):
            yield LLMChunk(text=word if i == 0 else f" {word}")
        yield LLMChunk(usage=Usage(prompt_tokens=100, completion_tokens=len(words)))

    async def aclose(self) -> None:
        self.closed = True
