import pytest

from ingestion.normalize import candidate_refs, extract_article_refs, normalize_article_ref


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Table from ARCHITECTURE §9.2
        ("L. 1237-19", "L1237-19"),
        ("l1237-19", "L1237-19"),
        ("article L 1237-19", "L1237-19"),
        ("R.1234-2", "R1234-2"),
        ("L1237-19-1", "L1237-19-1"),
        # Prefixes and spacing variants
        ("Article L1237-19", "L1237-19"),
        ("ARTICLE L.1237-19", "L1237-19"),
        ("art. L1237-19", "L1237-19"),
        ("art L1237-19", "L1237-19"),
        ("l'article L1237-19", "L1237-19"),
        ("l’article L1237-19", "L1237-19"),
        ("  L1237-19  ", "L1237-19"),
        ("L1237-19.", "L1237-19"),
        ("d1242-1", "D1242-1"),
        ("D. 1242-1", "D1242-1"),
        # Typographic dashes
        ("L1237–19", "L1237-19"),
        ("L1237—19", "L1237-19"),
        ("L1237‑19‑1", "L1237-19-1"),
        # Deep and old numbering
        ("L1237-19-14", "L1237-19-14"),
        ("L122-14-3", "L122-14-3"),
    ],
)
def test_normalize_article_ref(raw: str, expected: str) -> None:
    assert normalize_article_ref(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "1237-19",  # no letter: ambiguous
        "",
        "L",
        "L1237",  # no "-n" group
        "X1237-19",  # unknown letter
        "L12-1",  # too few digits
        "L12345-1",  # too many digits
        "L1237-19 et L1237-20",  # more than one reference
        "Quelle est la durée d'une période d'essai ?",
    ],
)
def test_normalize_article_ref_rejects(raw: str) -> None:
    assert normalize_article_ref(raw) is None


def test_candidate_refs_without_letter() -> None:
    assert candidate_refs("1237-19") == ["L1237-19", "R1237-19", "D1237-19"]
    assert candidate_refs("article 1234-9") == ["L1234-9", "R1234-9", "D1234-9"]


def test_candidate_refs_with_letter_is_unambiguous() -> None:
    assert candidate_refs("l. 1237-19") == ["L1237-19"]


def test_candidate_refs_garbage() -> None:
    assert candidate_refs("bonjour") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Que dit l'article L. 1237-19 ?", ["L1237-19"]),
        (
            "Voir les articles L1243-13, R. 1234-2 et l1243-13 (doublon).",
            ["L1243-13", "R1234-2"],
        ),
        ("Selon L1237-19-1 et L1237-19.", ["L1237-19-1", "L1237-19"]),
        ("Un CDD de 18 mois peut-il être renouvelé ?", []),
        # A letter glued to a preceding word is not a reference.
        ("CDD1243-13", []),
        # A bare number is not extracted (use candidate_refs for that).
        ("l'article 1237-19", []),
        ("art. D1242-1, al. 2", ["D1242-1"]),
    ],
)
def test_extract_article_refs(text: str, expected: list[str]) -> None:
    assert extract_article_refs(text) == expected


@pytest.mark.parametrize("raw", ["L. 1237-19", "R.1234-2", "L1237–19-1"])
def test_normalization_is_idempotent(raw: str) -> None:
    once = normalize_article_ref(raw)
    assert once is not None
    assert normalize_article_ref(once) == once
