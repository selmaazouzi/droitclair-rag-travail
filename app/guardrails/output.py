"""Output guardrails (ARCHITECTURE §10.3, §12): citation check and disclaimer."""

import re
from dataclasses import dataclass

DISCLAIMER = (
    "Information juridique générale, pas un conseil personnalisé. La convention collective "
    "ou le contrat de travail peuvent prévoir des dispositions plus favorables."
)

# "[2]", "[1, 3]", "[1;3]": groups of passage numbers in square brackets.
_CITATION_GROUP = re.compile(r"\[\s*(\d{1,3}(?:\s*[,;]\s*\d{1,3})*)\s*\]")
_NUMBER = re.compile(r"\d{1,3}")
UNCITED_MIN_CHARS = 200


@dataclass(frozen=True, slots=True)
class CitationCheck:
    cited: tuple[int, ...]  # valid passage numbers, in order of first appearance
    invalid: tuple[int, ...]  # numbers outside 1..n_passages
    uncited_answer: bool  # a substantial answer without any valid citation


def check_citations(answer: str, n_passages: int) -> CitationCheck:
    cited: dict[int, None] = {}
    invalid: dict[int, None] = {}
    for group in _CITATION_GROUP.finditer(answer):
        for number in map(int, _NUMBER.findall(group.group(1))):
            (cited if 1 <= number <= n_passages else invalid).setdefault(number, None)
    uncited = not cited and len(answer.strip()) >= UNCITED_MIN_CHARS
    return CitationCheck(cited=tuple(cited), invalid=tuple(invalid), uncited_answer=uncited)


def remove_invalid_citations(answer: str, n_passages: int) -> str:
    """Drop citation numbers that point to no passage; drop the brackets if none is left."""

    def keep_valid(match: re.Match[str]) -> str:
        numbers = [n for n in map(int, _NUMBER.findall(match.group(1))) if 1 <= n <= n_passages]
        return f"[{', '.join(map(str, numbers))}]" if numbers else ""

    cleaned = _CITATION_GROUP.sub(keep_valid, answer)
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", re.sub(r"[ \t]{2,}", " ", cleaned))
