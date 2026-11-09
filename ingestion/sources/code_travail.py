"""Code du travail loader: Légifrance table of contents + articles -> SourceDocument.

Field names and formats come from the inspection recorded in ARCHITECTURE §7.1.
"""

import asyncio
import logging
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
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


async def fetch_code_travail(client: LegifranceClient, *, on: date) -> list[SourceDocument]:
    """Fetch every in-scope article of the Code du travail in force on ``on``."""
    toc = await client.table_of_contents(CODE_DU_TRAVAIL_ID, on)
    selected = select_in_scope(toc)
    logger.info("Table of contents: %d in-scope articles in force on %s", len(selected), on)

    async def fetch(toc_article: TocArticle) -> SourceDocument:
        return parse_article(await client.get_article(toc_article.id), toc_article)

    documents = await asyncio.gather(*(fetch(a) for a in selected))
    return sorted(documents, key=lambda d: d.source_id)
