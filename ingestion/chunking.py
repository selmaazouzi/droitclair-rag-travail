"""Split documents into retrieval chunks (ARCHITECTURE §9.3).

- One article = one chunk (default). Optionally, above ``max_tokens``, split on alinéa
  boundaries with an overlap of one alinéa; an alinéa is never cut in the middle.
  bge-m3 accepts up to 8192 tokens, far above the longest article in scope.
- Every chunk starts with a header naming the source and its position in the code.

Token counts are approximated until the embedding tokenizer is wired in (phase 3):
French text averages roughly 3.5 characters per token with bge-m3's tokenizer.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

from ingestion.documents import SourceDocument, SourceType

TokenCounter = Callable[[str], int]

CHARS_PER_TOKEN = 3.5

_SOURCE_LABELS: dict[SourceType, str] = {
    "article": "Code du travail",
    "fiche_sp": "Fiche service-public.fr",
    "fiche_travail": "Fiche du ministère du Travail",
}


def approx_token_count(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    content: str


def chunk_header(doc: SourceDocument) -> str:
    lines = [f"[{_SOURCE_LABELS[doc.source_type]} — {doc.title}]"]
    if doc.hierarchy:
        lines.append(" > ".join(doc.hierarchy))
    return "\n".join(lines)


def _body_units(doc: SourceDocument) -> list[str]:
    units = list(doc.paragraphs)
    if doc.nota:
        units.append(f"NOTA : {doc.nota}")
    return units


def _group_with_overlap(units: list[str], budget: int, count: TokenCounter) -> list[list[str]]:
    """Greedy grouping under ``budget`` tokens.

    Each group starts with the last unit of the previous one (one-alinéa overlap).
    """
    groups: list[list[str]] = []
    current: list[str] = []
    for unit in units:
        candidate = [*current, unit]
        if current and count("\n".join(candidate)) > budget:
            groups.append(current)
            overlap = current[-1:]
            # Keep the overlap only if it leaves room for the new unit.
            current = [*overlap, unit] if count("\n".join([*overlap, unit])) <= budget else [unit]
        else:
            current = candidate
    if current:
        groups.append(current)
    return groups


def chunk_document(
    doc: SourceDocument,
    *,
    max_tokens: int | None = None,
    count_tokens: TokenCounter = approx_token_count,
) -> list[Chunk]:
    header = chunk_header(doc)
    units = _body_units(doc)
    whole = "\n".join([header, *units])
    if max_tokens is None or len(units) == 1 or count_tokens(whole) <= max_tokens:
        return [Chunk(index=0, content=whole)]

    budget = max(1, max_tokens - count_tokens(header + "\n"))
    groups = _group_with_overlap(units, budget, count_tokens)
    return [Chunk(index=i, content="\n".join([header, *group])) for i, group in enumerate(groups)]
