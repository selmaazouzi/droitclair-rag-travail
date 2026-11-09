import itertools
from dataclasses import replace
from datetime import date

from ingestion.chunking import chunk_document, chunk_header
from ingestion.documents import SourceDocument


def make_doc(
    paragraphs: tuple[str, ...] = ("Premier alinéa.",), **overrides: object
) -> SourceDocument:
    doc = SourceDocument(
        source_type="article",
        source_id="LEGIARTI000000000001",
        title="Article L1237-19",
        hierarchy=("Partie législative", "Livre II : Le contrat de travail", "Section 4 : Rupture"),
        url="https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000000000002",
        paragraphs=paragraphs,
        article_num="L1237-19",
        valid_from=date(2018, 1, 1),
    )
    return replace(doc, **overrides)  # type: ignore[arg-type]


def count_words(text: str) -> int:
    return len(text.split())


# --- SourceDocument -----------------------------------------------------------


def test_hierarchy_path_ends_with_title() -> None:
    assert make_doc().hierarchy_path == (
        "Partie législative > Livre II : Le contrat de travail > Section 4 : Rupture"
        " > Article L1237-19"
    )


def test_content_hash_is_stable() -> None:
    assert make_doc().content_hash == make_doc().content_hash
    assert len(make_doc().content_hash) == 64


def test_content_hash_changes_with_text_metadata_or_url() -> None:
    base = make_doc().content_hash
    assert make_doc(("Alinéa modifié.",)).content_hash != base
    assert make_doc(nota="Nouvelle note").content_hash != base
    assert make_doc(url="https://example.test/v2").content_hash != base
    assert make_doc(valid_from=date(2020, 1, 1)).content_hash != base


def test_dict_round_trip() -> None:
    doc = make_doc(("A.", "B."), nota="Note")
    assert SourceDocument.from_dict(doc.to_dict()) == doc
    assert doc.to_dict()["valid_from"] == "2018-01-01"


# --- Chunking -------------------------------------------------------------------


def test_header_names_source_and_hierarchy() -> None:
    assert chunk_header(make_doc()) == (
        "[Code du travail — Article L1237-19]\n"
        "Partie législative > Livre II : Le contrat de travail > Section 4 : Rupture"
    )


def test_short_article_is_one_chunk_with_header_and_nota() -> None:
    chunks = chunk_document(make_doc(("A.", "B."), nota="En vigueur en 2018."))
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].content.splitlines() == [
        "[Code du travail — Article L1237-19]",
        "Partie législative > Livre II : Le contrat de travail > Section 4 : Rupture",
        "A.",
        "B.",
        "NOTA : En vigueur en 2018.",
    ]


def test_long_article_is_one_chunk_by_default() -> None:
    paragraphs = tuple(f"Alinéa {i}. " + "mot " * 500 for i in range(10))  # ~6000 tokens
    chunks = chunk_document(make_doc(paragraphs))
    assert len(chunks) == 1
    assert chunks[0].content.count("Alinéa ") == 10


def test_long_article_is_split_on_alineas_with_one_alinea_overlap() -> None:
    paragraphs = tuple(f"alinea{i}" + " mot" * 9 for i in range(6))  # 10 words each
    header_words = count_words(chunk_header(make_doc()))
    chunks = chunk_document(
        make_doc(paragraphs), max_tokens=header_words + 25, count_tokens=count_words
    )

    bodies = [c.content.splitlines()[2:] for c in chunks]
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert all(c.content.startswith("[Code du travail — Article L1237-19]") for c in chunks)
    assert all(count_words(c.content) <= header_words + 25 for c in chunks)
    # Consecutive chunks share exactly one alinéa, and together cover every alinéa in order.
    for previous, current in itertools.pairwise(bodies):
        assert previous[-1] == current[0]
    covered = [bodies[0][0]] + [line for body in bodies for line in body[1:]]
    assert list(dict.fromkeys(covered)) == list(paragraphs)


def test_single_oversized_alinea_is_never_cut() -> None:
    doc = make_doc(("mot " * 2000,))
    chunks = chunk_document(doc, max_tokens=50)
    assert len(chunks) == 1
    assert chunks[0].content.endswith("mot " * 1999 + "mot ")
