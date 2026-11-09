"""LLM interface (ARCHITECTURE §3.4): providers are interchangeable behind ``LLMClient``."""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None  # reported by paid APIs (OpenRouter); None for local models


@dataclass(frozen=True, slots=True)
class LLMChunk:
    """A piece of streamed answer; the last chunk carries the token usage."""

    text: str = ""
    usage: Usage | None = None


class LLMClient(Protocol):
    @property
    def model(self) -> str: ...

    def stream(
        self, messages: Sequence[ChatMessage], *, temperature: float
    ) -> AsyncIterator[LLMChunk]: ...

    async def aclose(self) -> None: ...
