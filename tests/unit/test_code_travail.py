import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from ingestion.filters import is_article_in_scope
from ingestion.sources.code_travail import (
    TocArticle,
    clean_code_travail,
    download_code_travail,
    iter_toc_articles,
    load_code_travail,
    parse_article,
    select_in_scope,
)
from ingestion.sources.legifrance import LegifranceClient

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


# --- Snapshot on disk: download (API -> data/) and load (data/ -> documents) ------

REAL_ID = "LEGIARTI000035623969"  # id of the fixture article L1237-19

SNAPSHOT_TOC: dict[str, Any] = {
    "sections": [
        {
            "title": "Partie législative",
            "etat": "VIGUEUR",
            "sections": [],
            "articles": [
                {
                    "id": REAL_ID,
                    "cid": "LEGIARTI000035610511",
                    "num": "L1237-19",
                    "etat": "VIGUEUR",
                },
                {"id": "OLD", "cid": "C-OLD", "num": "L1237-20", "etat": "ABROGE"},
            ],
        }
    ]
}


def fake_legifrance(article: dict[str, Any], calls: list[str]) -> LegifranceClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.url.path.endswith("/consult/legi/tableMatieres"):
            return httpx.Response(200, json=SNAPSHOT_TOC)
        return httpx.Response(200, json=article)

    async def no_sleep(_: float) -> None:
        return None

    return LegifranceClient(
        api_url="https://api.test",
        oauth_url="https://oauth.test",
        client_id="id",
        client_secret="secret",
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
    )


async def test_download_writes_raw_snapshot_then_reuses_it(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    calls: list[str] = []
    (tmp_path / "articles_bruts").mkdir()
    (tmp_path / "articles_bruts" / "STALE.json").write_text("{}")

    async with fake_legifrance(payload, calls) as client:
        first = await download_code_travail(client, tmp_path, on=date(2026, 11, 5))
        second = await download_code_travail(client, tmp_path, on=date(2026, 11, 5))

    assert (first.total, first.downloaded, first.reused, first.removed) == (1, 1, 0, 1)
    assert (second.total, second.downloaded, second.reused, second.removed) == (1, 0, 1, 0)
    assert calls.count("/consult/getArticle") == 1  # the second run reads from disk
    raw_files = sorted(p.name for p in (tmp_path / "articles_bruts").iterdir())
    assert raw_files == [f"{REAL_ID}.json"]
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["in_force_on"] == "2026-11-05" and manifest["article_count"] == 1
    # Files are indented, one key per line, so they are readable in an editor.
    article_text = (tmp_path / "articles_bruts" / f"{REAL_ID}.json").read_text()
    assert article_text.startswith('{\n  "') and '\n    "num": "L1237-19",' in article_text


async def test_download_force_refetches(tmp_path: Path, payload: dict[str, Any]) -> None:
    calls: list[str] = []
    async with fake_legifrance(payload, calls) as client:
        await download_code_travail(client, tmp_path, on=date(2026, 11, 5))
        summary = await download_code_travail(client, tmp_path, on=date(2026, 11, 5), force=True)
    assert summary.downloaded == 1
    assert calls.count("/consult/getArticle") == 2


def write_raw_snapshot(dest: Path, payload: dict[str, Any]) -> None:
    (dest / "articles_bruts").mkdir(parents=True)
    (dest / "toc.json").write_text(json.dumps(SNAPSHOT_TOC))
    (dest / "manifest.json").write_text(json.dumps({"article_count": 1}))
    (dest / "articles_bruts" / f"{REAL_ID}.json").write_text(json.dumps(payload))


def test_clean_keeps_only_indexed_fields(tmp_path: Path, payload: dict[str, Any]) -> None:
    write_raw_snapshot(tmp_path, payload)
    (tmp_path / "articles").mkdir()
    (tmp_path / "articles" / "L9999-1.json").write_text("{}")  # no longer in the snapshot

    summary = clean_code_travail(tmp_path)

    assert (summary.written, summary.removed) == (1, 1)
    assert sorted(p.name for p in (tmp_path / "articles").iterdir()) == ["L1237-19.json"]
    cleaned = json.loads((tmp_path / "articles" / "L1237-19.json").read_text())
    assert list(cleaned) == [
        "source_type",
        "source_id",
        "article_num",
        "title",
        "url",
        "valid_from",
        "source_updated",
        "hierarchy",
        "paragraphs",
        "nota",
    ]
    assert cleaned["source_id"] == "LEGIARTI000035610511"
    assert cleaned["valid_from"] == "2018-01-01"
    assert cleaned["hierarchy"] == ["Partie législative"]
    assert len(cleaned["paragraphs"]) == 2 and "<" not in "".join(cleaned["paragraphs"])


def test_load_reads_cleaned_files(tmp_path: Path, payload: dict[str, Any]) -> None:
    write_raw_snapshot(tmp_path, payload)
    clean_code_travail(tmp_path)

    documents = load_code_travail(tmp_path)

    assert documents == [parse_article(payload, TOC_L1237_19_FROM_SNAPSHOT)]


TOC_L1237_19_FROM_SNAPSHOT = TocArticle(
    id=REAL_ID,
    cid="LEGIARTI000035610511",
    num="L1237-19",
    etat="VIGUEUR",
    hierarchy=("Partie législative",),
)


def test_clean_without_snapshot_explains_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"ingestion\.cli fetch"):
        clean_code_travail(tmp_path)


def test_clean_incomplete_raw_snapshot_fails(tmp_path: Path, payload: dict[str, Any]) -> None:
    write_raw_snapshot(tmp_path, payload)
    (tmp_path / "articles_bruts" / f"{REAL_ID}.json").unlink()
    with pytest.raises(FileNotFoundError, match="incomplete"):
        clean_code_travail(tmp_path)


def test_load_before_clean_explains_what_to_do(tmp_path: Path, payload: dict[str, Any]) -> None:
    write_raw_snapshot(tmp_path, payload)  # fetched, but `clean` never ran
    with pytest.raises(ValueError, match=r"ingestion\.cli clean"):
        load_code_travail(tmp_path)
