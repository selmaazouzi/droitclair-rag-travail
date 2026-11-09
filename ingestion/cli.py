"""Ingestion entry point (ARCHITECTURE §9).

    python -m ingestion.cli run [--dry-run] [--only articles] [--date YYYY-MM-DD]

--dry-run runs the whole pipeline, including the database comparison, inside a
transaction that is rolled back: it reports what would change without writing.
"""

import argparse
import asyncio
import logging
import sys
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.config import get_settings
from app.db.session import dispose_engine, get_sessionmaker
from ingestion.chunking import approx_token_count, chunk_document
from ingestion.documents import SourceDocument, SourceType
from ingestion.index import record_run, sync_documents
from ingestion.sources.code_travail import fetch_code_travail
from ingestion.sources.legifrance import LegifranceClient

logger = logging.getLogger("ingestion")


@dataclass(frozen=True)
class Source:
    source_types: tuple[SourceType, ...]
    load: Callable[[date], Awaitable[list[SourceDocument]]]


async def _load_articles(on: date) -> list[SourceDocument]:
    async with LegifranceClient.from_settings(get_settings()) as client:
        return await fetch_code_travail(client, on=on)


SOURCES: dict[str, Source] = {
    "articles": Source(source_types=("article",), load=_load_articles),
    # "fiches": added with the fiches loaders.
}


def print_report(documents: Sequence[SourceDocument]) -> None:
    """Articles per part and title, plus chunk statistics (phase 1 exit criterion)."""
    by_title = Counter(
        " > ".join((d.hierarchy[0], d.hierarchy[3])) if len(d.hierarchy) > 3 else d.source_type
        for d in documents
    )
    print(f"\n{len(documents)} documents")
    for key, count in sorted(by_title.items()):
        print(f"  {count:5d}  {key}")

    chunks = [c for d in documents for c in chunk_document(d)]
    tokens = sorted(approx_token_count(c.content) for c in chunks)
    split = sum(1 for d in documents if len(chunk_document(d)) > 1)
    if tokens:
        print(
            f"{len(chunks)} chunks ({split} documents split) — approx. tokens per chunk: "
            f"median {tokens[len(tokens) // 2]}, max {tokens[-1]}\n"
        )


async def run(only: str | None, on: date, dry_run: bool) -> int:
    started_at = datetime.now(UTC)
    selected = {only: SOURCES[only]} if only else SOURCES

    documents: list[SourceDocument] = []
    source_types: list[SourceType] = []
    for name, source in selected.items():
        logger.info("Loading %s (in force on %s)", name, on)
        documents.extend(await source.load(on))
        source_types.extend(source.source_types)
    print_report(documents)

    try:
        async with get_sessionmaker()() as session, session.begin():
            stats = await sync_documents(session, documents, source_types=source_types)
            run_row = await record_run(session, stats, started_at)
            print(
                f"added={stats.added} updated={stats.updated} removed={stats.removed} "
                f"unchanged={stats.unchanged} index_version={run_row.index_version}"
            )
            if dry_run:
                await session.rollback()
                print("Dry run: nothing written.")
    finally:
        await dispose_engine()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ingestion.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="fetch sources and sync the index")
    run_parser.add_argument("--dry-run", action="store_true", help="report changes, write nothing")
    run_parser.add_argument("--only", choices=sorted(SOURCES), help="ingest a single source")
    run_parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="version of the texts in force on this date (default: today)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    on = args.date or datetime.now(UTC).date()
    return asyncio.run(run(args.only, on, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
