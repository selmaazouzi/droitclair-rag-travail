import pytest

from app.guardrails.output import check_citations, remove_invalid_citations


def test_valid_citations_in_order_of_first_appearance() -> None:
    check = check_citations("Deux fois [3]. Voir aussi [1] et encore [3].", n_passages=6)
    assert check.cited == (3, 1)
    assert check.invalid == ()
    assert not check.uncited_answer


@pytest.mark.parametrize("text", ["[1, 2]", "[1,2]", "[1; 2]", "[ 1 , 2 ]", "[1][2]"])
def test_grouped_citations(text: str) -> None:
    assert check_citations(text, n_passages=6).cited == (1, 2)


def test_out_of_range_citations_are_invalid() -> None:
    check = check_citations("Selon [2] et [7], mais pas [0].", n_passages=6)
    assert check.cited == (2,)
    assert check.invalid == (7, 0)


def test_bracketed_text_is_not_a_citation() -> None:
    assert check_citations("[Code du travail — Article L1221-19]", n_passages=6).cited == ()


def test_long_answer_without_citation_is_flagged() -> None:
    assert check_citations("Texte sans source. " * 20, n_passages=6).uncited_answer
    assert not check_citations("Je ne sais pas.", n_passages=6).uncited_answer


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Deux fois [2].", "Deux fois [2]."),
        ("Deux fois [9].", "Deux fois."),
        ("Deux fois [2, 9].", "Deux fois [2]."),
        ("Voir [8] puis [1].", "Voir puis [1]."),
    ],
)
def test_remove_invalid_citations(answer: str, expected: str) -> None:
    assert remove_invalid_citations(answer, n_passages=6) == expected
