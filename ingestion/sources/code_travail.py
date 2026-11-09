"""Code du travail loader: Légifrance table of contents + articles -> SourceDocument.

Field names and formats come from the inspection recorded in ARCHITECTURE §7.1.
"""

import asyncio
import json
import logging
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ingestion.documents import SourceDocument
from ingestion.filters import is_article_in_scope
from ingestion.html_text import html_to_paragraphs
from ingestion.normalize import normalize_article_ref
from ingestion.sources.legifrance import LegifranceClient

logger = logging.getLogger(__name__)

CODE_DU_TRAVAIL_ID = "LEGITEXT000006072050"
ARTICLE_URL = "https://www.legifrance.gouv.fr/codes/article_lc/{article_id}"
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class TocArticle:
    id: str  # version id (LEGIARTI...), used to fetch the text
    cid: str  # stable id across versions
    num: str
    etat: str
    hierarchy: tuple[str, ...]


def _clean(text: str | None) -> str:
    return _WHITESPACE.sub(" ", text or "").strip()


def iter_toc_articles(node: Mapping[str, Any], trail: tuple[str, ...] = ()) -> Iterator[TocArticle]:
    """Walk the table of contents recursively, rebuilding each article's section path."""
    for article in node.get("articles") or []:
        yield TocArticle(
            id=article["id"],
            cid=article["cid"],
            num=_clean(article.get("num")),
            etat=article.get("etat") or "",
            hierarchy=trail,
        )
    for section in node.get("sections") or []:
        yield from iter_toc_articles(section, (*trail, _clean(section.get("title"))))


def select_in_scope(toc: Mapping[str, Any]) -> list[TocArticle]:
    return [a for a in iter_toc_articles(toc) if is_article_in_scope(a.num, a.etat)]


def _epoch_ms_to_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC).date()


def parse_article(payload: Mapping[str, Any], toc_article: TocArticle) -> SourceDocument:
    article = payload["article"]
    if article.get("id") != toc_article.id:
        raise ValueError(f"Expected article {toc_article.id}, got {article.get('id')}")

    num = normalize_article_ref(article.get("num") or toc_article.num)
    if num is None:
        raise ValueError(f"Unparseable article number {article.get('num')!r} ({toc_article.id})")

    paragraphs = tuple(html_to_paragraphs(article.get("texteHtml") or ""))
    if not paragraphs:  # fall back on the plain text, which loses alinéa breaks
        paragraphs = tuple(p for p in [_clean(article.get("texte"))] if p)
    if not paragraphs:
        raise ValueError(f"Article {num} ({toc_article.id}) has no text")

    return SourceDocument(
        source_type="article",
        source_id=toc_article.cid,
        title=f"Article {num}",
        hierarchy=toc_article.hierarchy,
        url=ARTICLE_URL.format(article_id=toc_article.id),
        paragraphs=paragraphs,
        article_num=num,
        nota=_clean(article.get("nota")) or None,
        valid_from=_epoch_ms_to_date(article.get("dateDebut")),
    )


# --- Snapshot on disk --------------------------------------------------------------
#
#   <data_dir>/legifrance/code_travail/
#       manifest.json              what was fetched, and when
#       toc.json                   table of contents (structure, no text)
#       articles_bruts/<id>.json   raw getArticle response per in-scope article version (fetch)
#       articles/<num>.json        cleaned article: only what is indexed (clean)

RAW_DIR = "articles_bruts"
CLEAN_DIR = "articles"


@dataclass(frozen=True, slots=True)
class DownloadSummary:
    total: int
    downloaded: int
    reused: int
    removed: int


@dataclass(frozen=True, slots=True)
class CleanSummary:
    written: int
    removed: int


def snapshot_dir(data_dir: Path) -> Path:
    return data_dir / "legifrance" / "code_travail"


def _write_json(path: Path, payload: object) -> None:
    """Write indented JSON (readable in an editor), atomically: an interrupted
    run never leaves a truncated file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _remove_stale(directory: Path, wanted: set[str]) -> int:
    stale = [p for p in directory.glob("*.json") if p.name not in wanted]
    for path in stale:
        path.unlink()
    return len(stale)


async def download_code_travail(
    client: LegifranceClient, dest: Path, *, on: date, force: bool = False
) -> DownloadSummary:
    """Save the raw in-scope articles in force on ``on`` under ``dest/articles_bruts``.

    An article version (LEGIARTI id) never changes, so files already on disk are
    reused unless ``force``; files of versions no longer in scope are removed.
    """
    toc = await client.table_of_contents(CODE_DU_TRAVAIL_ID, on)
    selected = select_in_scope(toc)
    logger.info("Table of contents: %d in-scope articles in force on %s", len(selected), on)

    raw_dir = dest / RAW_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    missing = [a for a in selected if force or not (raw_dir / f"{a.id}.json").exists()]

    async def fetch(toc_article: TocArticle) -> None:
        payload = await client.get_article(toc_article.id)
        _write_json(raw_dir / f"{toc_article.id}.json", payload)

    await asyncio.gather(*(fetch(a) for a in missing))

    # The TOC is written last: a snapshot never references articles that are not on disk.
    _write_json(dest / "toc.json", toc)
    removed = _remove_stale(raw_dir, {f"{a.id}.json" for a in selected})
    _write_json(
        dest / "manifest.json",
        {
            "text_id": CODE_DU_TRAVAIL_ID,
            "in_force_on": on.isoformat(),
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "article_count": len(selected),
        },
    )
    return DownloadSummary(
        total=len(selected),
        downloaded=len(missing),
        reused=len(selected) - len(missing),
        removed=removed,
    )


def clean_code_travail(snapshot: Path) -> CleanSummary:
    """Raw articles -> cleaned articles (``articles/<num>.json``). No network access.

    A cleaned article holds only what is indexed: identifiers, number, position in
    the code, URL, entry-into-force date, alinéas as plain text, and the nota.
    """
    toc_path = snapshot / "toc.json"
    if not toc_path.exists():
        raise FileNotFoundError(
            f"No snapshot in {snapshot}: run `python -m ingestion.cli fetch` first."
        )
    selected = select_in_scope(json.loads(toc_path.read_text(encoding="utf-8")))

    clean_dir = snapshot / CLEAN_DIR
    clean_dir.mkdir(parents=True, exist_ok=True)
    written: set[str] = set()
    for toc_article in selected:
        raw_path = snapshot / RAW_DIR / f"{toc_article.id}.json"
        if not raw_path.exists():
            raise FileNotFoundError(
                f"{raw_path} is missing: snapshot incomplete, run `python -m ingestion.cli fetch`."
            )
        doc = parse_article(json.loads(raw_path.read_text(encoding="utf-8")), toc_article)
        name = f"{doc.article_num}.json"
        if name in written:
            raise ValueError(f"Two in-scope articles share the number {doc.article_num}")
        _write_json(clean_dir / name, doc.to_dict())
        written.add(name)

    return CleanSummary(written=len(written), removed=_remove_stale(clean_dir, written))


def load_code_travail(snapshot: Path) -> list[SourceDocument]:
    """Read the cleaned articles written by ``clean_code_travail``."""
    manifest_path = snapshot / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"No snapshot in {snapshot}: run `python -m ingestion.cli fetch` first."
        )
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))["article_count"]
    documents = [
        SourceDocument.from_dict(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((snapshot / CLEAN_DIR).glob("*.json"))
    ]
    if len(documents) != expected:
        raise ValueError(
            f"{len(documents)} cleaned articles in {snapshot / CLEAN_DIR} but the snapshot has "
            f"{expected}: run `python -m ingestion.cli clean`."
        )
    return sorted(documents, key=lambda d: d.source_id)
