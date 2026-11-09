"""Canonical form of Code du travail article references (ARCHITECTURE §9.2).

Used both at ingestion time and on user queries, so that "L. 1237-19",
"l1237-19" and "article L 1237-19" all resolve to the same key: ``L1237-19``.
"""

import re

_DASHES = "-‐‑‒–—"  # hyphen-minus, hyphen, non-breaking hyphen, figure dash, en dash, em dash
_DASH_CLASS = f"[{_DASHES}]"

# A number is 3 or 4 digits followed by one or more "-<digits>" groups:
# L1237-19, L1237-19-1, and the pre-2008 numbering L122-14.
_NUMBER = rf"\d{{3,4}}(?:{_DASH_CLASS}\d+)+"
_LETTER_REF = rf"(?P<letter>[LRD])\s*\.?\s*(?P<number>{_NUMBER})"
_ARTICLE_WORD = r"(?:(?:l['’]\s*)?(?:article|art\.?)\s+)?"

_STRICT = re.compile(rf"^\s*{_ARTICLE_WORD}{_LETTER_REF}\s*\.?\s*$", re.IGNORECASE)
_BARE = re.compile(rf"^\s*{_ARTICLE_WORD}(?P<number>{_NUMBER})\s*\.?\s*$", re.IGNORECASE)
_IN_TEXT = re.compile(rf"(?<!\w){_LETTER_REF}(?!\d)", re.IGNORECASE)
_DASH_RE = re.compile(_DASH_CLASS)

_PREFIXES = ("L", "R", "D")


def _canonical(letter: str, number: str) -> str:
    return f"{letter.upper()}{_DASH_RE.sub('-', number)}"


def normalize_article_ref(text: str) -> str | None:
    """Return the canonical reference if ``text`` is exactly one lettered reference.

    >>> normalize_article_ref("article L. 1237-19")
    'L1237-19'
    >>> normalize_article_ref("1237-19") is None  # ambiguous: see candidate_refs
    True
    """
    match = _STRICT.match(text)
    if match is None:
        return None
    return _canonical(match["letter"], match["number"])


def candidate_refs(text: str) -> list[str]:
    """Like ``normalize_article_ref``, but expands a number without letter to L, R and D.

    >>> candidate_refs("1237-19")
    ['L1237-19', 'R1237-19', 'D1237-19']
    """
    strict = normalize_article_ref(text)
    if strict is not None:
        return [strict]
    match = _BARE.match(text)
    if match is None:
        return []
    return [_canonical(prefix, match["number"]) for prefix in _PREFIXES]


def extract_article_refs(text: str) -> list[str]:
    """Find every lettered article reference in free text, deduplicated, in order of appearance.

    >>> extract_article_refs("Voir les articles L. 1237-19 et R1234-2, puis L1237-19.")
    ['L1237-19', 'R1234-2']
    """
    refs: dict[str, None] = {}
    for match in _IN_TEXT.finditer(text):
        refs.setdefault(_canonical(match["letter"], match["number"]), None)
    return list(refs)
