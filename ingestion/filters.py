"""V1 scope (ARCHITECTURE §2 and §9.1): Code du travail, Livre II, titres II to IV."""

import re

from ingestion.normalize import normalize_article_ref

SCOPE_PATTERN = re.compile(r"^[LRD]12[2-4]\d")  # applied to the normalised number
IN_FORCE = "VIGUEUR"


def is_article_in_scope(article_num: str | None, etat: str | None) -> bool:
    if not article_num or etat != IN_FORCE:
        return False
    normalized = normalize_article_ref(article_num)
    return normalized is not None and SCOPE_PATTERN.match(normalized) is not None
