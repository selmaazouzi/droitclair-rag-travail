import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ingestion.filters import is_article_in_scope
from ingestion.sources.code_travail import (
    TocArticle,
    iter_toc_articles,
    parse_article,
    select_in_scope,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "legifrance"


def toc_article(num: str, etat: str = "VIGUEUR", id_: str = "LEGIARTI1") -> dict[str, Any]:
    return {"id": id_, "cid": f"C{id_}", "num": num, "etat": etat}


# Same shape as /consult/legi/tableMatieres (ARCHITECTURE §7.1), trimmed.
MINI_TOC: dict[str, Any] = {
    "cid": "LEGITEXT000006072050",
    "title": "Code du travail",
    "articles": [],
    "sections": [
        {
            "title": "Partie législative  ",
            "etat": "VIGUEUR",
            "articles": [],
            "sections": [
                {
                    "title": "Livre II : Le contrat de travail",
                    "etat": "VIGUEUR",
                    "articles": [toc_article("L1221-19", id_="A1")],
                    "sections": [
                        {
                            "title": "Section 4 :  Rupture  conventionnelle",
                            "etat": "VIGUEUR",
                            "sections": [],
                            "articles": [
                                toc_article("L1237-19", id_="A2"),
                                toc_article("L1237-20", etat="ABROGE", id_="A3"),
                                toc_article("L1251-1", id_="A4"),  # Titre V: out of V1 scope
                            ],
                        }
                    ],
                }
            ],
        },
        {
            "title": "Partie législative ancienne",
            "etat": "VIGUEUR",
            "sections": [],
            "articles": [toc_article("L122-14-3", id_="A5")],  # old numbering
        },
    ],
}


# --- Scope filter ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("num", "etat", "expected"),
    [
        ("L1221-19", "VIGUEUR", True),
        ("R1234-2", "VIGUEUR", True),
        ("D1242-1", "VIGUEUR", True),
        ("L1249-1", "VIGUEUR", True),
        ("L. 1237-19", "VIGUEUR", True),  # filter applies to the normalised number
        ("L1237-19", "ABROGE", False),
        ("L1237-19", "VIGUEUR_DIFF", False),
        ("L1211-1", "VIGUEUR", False),  # Titre I
        ("L1251-1", "VIGUEUR", False),  # Titre V (intérim)
        ("L3121-1", "VIGUEUR", False),  # durée du travail
        ("L122-14-3", "VIGUEUR", False),  # old numbering
        (None, "VIGUEUR", False),
        ("", "VIGUEUR", False),
    ],
)
def test_is_article_in_scope(num: str | None, etat: str, expected: bool) -> None:
    assert is_article_in_scope(num, etat) is expected


# --- Table of contents ----------------------------------------------------------


def test_iter_toc_rebuilds_cleaned_hierarchy() -> None:
    articles = {a.num: a for a in iter_toc_articles(MINI_TOC)}
    assert articles["L1221-19"].hierarchy == (
        "Partie législative",
        "Livre II : Le contrat de travail",
    )
    assert articles["L1237-19"].hierarchy == (
        "Partie législative",
        "Livre II : Le contrat de travail",
        "Section 4 : Rupture conventionnelle",
    )
    assert articles["L1237-19"].cid == "CA2"


def test_select_in_scope_keeps_only_in_force_v1_articles() -> None:
    assert [a.num for a in select_in_scope(MINI_TOC)] == ["L1221-19", "L1237-19"]


# --- Article --------------------------------------------------------------------


@pytest.fixture
def payload() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "article_L1237-19.json").read_text())
    return data


TOC_L1237_19 = TocArticle(
    id="LEGIARTI000035623969",
    cid="LEGIARTI000035610511",
    num="L1237-19",
    etat="VIGUEUR",
    hierarchy=("Partie législative", "Section 4"),
)


def test_parse_real_article(payload: dict[str, Any]) -> None:
    doc = parse_article(payload, TOC_L1237_19)

    assert doc.source_type == "article"
    assert doc.source_id == "LEGIARTI000035610511"  # stable cid, not the version id
    assert doc.article_num == "L1237-19"
    assert doc.title == "Article L1237-19"
    assert doc.hierarchy == ("Partie législative", "Section 4")
    assert doc.url == "https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000035623969"
    assert doc.valid_from == date(2018, 1, 1)  # 1514764800000 ms
    assert doc.paragraphs == (
        "Un accord collectif peut déterminer le contenu d'une rupture conventionnelle collective "
        "excluant tout licenciement pour atteindre les objectifs qui lui sont assignés en termes "
        "de suppression d'emplois.",
        "L'administration est informée sans délai de l'ouverture d'une négociation en vue de "
        "l'accord précité.",
    )
    assert doc.nota is not None and doc.nota.startswith("Conformément aux dispositions du X")


def test_parse_article_rejects_mismatched_id(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="Expected article"):
        parse_article(payload, TocArticle("OTHER", "C", "L1237-19", "VIGUEUR", ()))


def test_parse_article_falls_back_on_plain_text(payload: dict[str, Any]) -> None:
    payload["article"]["texteHtml"] = "<p></p>"
    doc = parse_article(payload, TOC_L1237_19)
    assert len(doc.paragraphs) == 1
    assert doc.paragraphs[0].startswith("Un accord collectif")


def test_parse_article_without_text_fails(payload: dict[str, Any]) -> None:
    payload["article"]["texteHtml"] = ""
    payload["article"]["texte"] = "  "
    with pytest.raises(ValueError, match="no text"):
        parse_article(payload, TOC_L1237_19)


def test_parse_article_without_nota(payload: dict[str, Any]) -> None:
    payload["article"]["nota"] = ""
    assert parse_article(payload, TOC_L1237_19).nota is None
