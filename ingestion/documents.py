"""Source-agnostic document produced by every loader and consumed by chunking and indexing."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, cast

SourceType = Literal["article", "fiche_sp", "fiche_travail"]


@dataclass(frozen=True, slots=True)
class SourceDocument:
    source_type: SourceType
    source_id: str  # stable across versions (article: LEGIARTI cid)
    title: str  # e.g. "Article L1237-19"
    hierarchy: tuple[str, ...]  # e.g. ("Partie législative", ..., "Section 4 : ...")
    url: str
    paragraphs: tuple[str, ...]  # alinéas, in order; never empty
    article_num: str | None = None  # normalised, e.g. "L1237-19"
    nota: str | None = None
    valid_from: date | None = None
    source_updated: date | None = None

    def to_dict(self) -> dict[str, Any]:
        """Readable JSON form, used for the cleaned files in data/ (keys in reading order)."""
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "article_num": self.article_num,
            "title": self.title,
            "url": self.url,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "source_updated": self.source_updated.isoformat() if self.source_updated else None,
            "hierarchy": list(self.hierarchy),
            "paragraphs": list(self.paragraphs),
            "nota": self.nota,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceDocument":
        def as_date(value: str | None) -> date | None:
            return date.fromisoformat(value) if value else None

        return cls(
            source_type=cast(SourceType, data["source_type"]),
            source_id=data["source_id"],
            title=data["title"],
            hierarchy=tuple(data["hierarchy"]),
            url=data["url"],
            paragraphs=tuple(data["paragraphs"]),
            article_num=data.get("article_num"),
            nota=data.get("nota"),
            valid_from=as_date(data.get("valid_from")),
            source_updated=as_date(data.get("source_updated")),
        )

    @property
    def hierarchy_path(self) -> str:
        return " > ".join((*self.hierarchy, self.title))

    @property
    def content_hash(self) -> str:
        """SHA-256 of every field that ends up in the index (ARCHITECTURE §9.4).

        Any change to the text, the metadata or the URL marks the document as updated.
        """
        canonical = json.dumps(
            {
                "source_type": self.source_type,
                "source_id": self.source_id,
                "title": self.title,
                "hierarchy": self.hierarchy,
                "url": self.url,
                "paragraphs": self.paragraphs,
                "article_num": self.article_num,
                "nota": self.nota,
                "valid_from": self.valid_from.isoformat() if self.valid_from else None,
                "source_updated": self.source_updated.isoformat() if self.source_updated else None,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
