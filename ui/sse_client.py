"""Read a Server-Sent Events stream (``event:`` / ``data:`` lines, blank line = end of event).

Kept apart from the Chainlit app so it can be tested without Chainlit installed.
"""

import json
from collections.abc import AsyncIterator, Iterable, Iterator
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SSEEvent:
    event: str
    data: Any  # decoded JSON


class _Accumulator:
    def __init__(self) -> None:
        self.event = "message"
        self.data: list[str] = []

    def feed(self, line: str) -> SSEEvent | None:
        """Consume one line; return an event when a blank line closes it."""
        if line == "":
            if not self.data:
                return None
            event = SSEEvent(self.event, json.loads("\n".join(self.data)))
            self.event, self.data = "message", []
            return event
        if line.startswith(":"):  # comment / keep-alive
            return None
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "event":
            self.event = value
        elif field == "data":
            self.data.append(value)
        return None


def parse_sse(lines: Iterable[str]) -> Iterator[SSEEvent]:
    acc = _Accumulator()
    for line in lines:
        if (event := acc.feed(line.rstrip("\r"))) is not None:
            yield event
    if (event := acc.feed("")) is not None:  # stream ended without a final blank line
        yield event


async def aparse_sse(lines: AsyncIterator[str]) -> AsyncIterator[SSEEvent]:
    acc = _Accumulator()
    async for line in lines:
        if (event := acc.feed(line.rstrip("\r"))) is not None:
            yield event
    if (event := acc.feed("")) is not None:
        yield event
