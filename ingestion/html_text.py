"""Convert Légifrance article HTML into a list of alinéas (ARCHITECTURE §7.1).

Observed markup in the V1 scope: alinéas are separated either by ``<p>`` blocks or
by ``<br/><br/>`` inside a ``<p>``; inline ``<a>`` and ``<sup>``; and one table
(L1235-3, the dismissal compensation scale), rendered here one row per line with
cells separated by " | ".
"""

import re
from html.parser import HTMLParser

_BLOCK_TAGS = frozenset(
    {"p", "div", "center", "table", "tbody", "thead", "ul", "ol", "li", "h1", "h2", "h3", "h4"}
)
_CELL_TAGS = frozenset({"td", "th"})
_WHITESPACE = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


class _AlineaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self._buffer: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._after_link = False

    def _flush(self) -> None:
        text = _clean("".join(self._buffer))
        if text:
            self.paragraphs.append(text)
        self._buffer = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._cell is not None:
            if tag == "br" or tag in _BLOCK_TAGS:
                self._cell.append(" ")
        elif tag == "tr":
            self._flush()
            self._row = []
        elif tag in _CELL_TAGS and self._row is not None:
            self._cell = []
        elif tag == "br" or tag in _BLOCK_TAGS:
            self._flush()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._after_link = True
        elif tag in _CELL_TAGS and self._cell is not None and self._row is not None:
            self._row.append(_clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            row = " | ".join(self._row)
            if row.strip(" |"):
                self.paragraphs.append(row)
            self._row = None
        elif self._cell is None and tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._after_link and data[:1].isalnum():
            # The source sometimes omits the space after a link ("L. 1233-87</a>procède");
            # Légifrance's own plain-text rendering inserts one.
            data = " " + data
        self._after_link = False
        (self._cell if self._cell is not None else self._buffer).append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def html_to_paragraphs(html: str) -> list[str]:
    parser = _AlineaParser()
    parser.feed(html)
    parser.close()
    return parser.paragraphs
