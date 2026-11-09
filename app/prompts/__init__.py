"""Versioned prompts (ARCHITECTURE §3.3): one text file per version, e.g. ``system_v1.txt``.

A prompt is never edited in place once used in an evaluation: create ``system_v2.txt``
and switch PROMPT_SYSTEM_VERSION, so every result stays traceable to its prompt.
"""

from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


@dataclass(frozen=True, slots=True)
class Prompt:
    version: str
    text: str


def load_prompt(version: str) -> Prompt:
    path = PROMPTS_DIR / f"{version}.txt"
    if not path.is_file():
        available = sorted(p.stem for p in PROMPTS_DIR.glob("*.txt"))
        raise FileNotFoundError(f"Unknown prompt {version!r}; available: {available}")
    return Prompt(version=version, text=path.read_text(encoding="utf-8").strip())
