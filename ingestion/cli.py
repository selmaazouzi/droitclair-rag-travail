"""Ingestion entry point (ARCHITECTURE §9). Three steps, one folder each:

    python -m ingestion.cli fetch [--date YYYY-MM-DD] [--force]   # API -> articles_bruts/ (+ clean)
    python -m ingestion.cli clean                                 # articles_bruts/ -> articles/
    python -m ingestion.cli run [--dry-run] [--only articles]     # articles/ -> PostgreSQL

Only `fetch` uses the network. `clean` is run by `fetch`; run it alone after
changing the cleaning code.

`run --dry-run` runs the whole pipeline, including the database comparison, inside a
transaction that is rolled back: it reports what would change without writing.
"""

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path

from app.config import get_settings
from app.db.session import dispose_engine, get_sessionmaker
from ingestion.chunking import approx_token_count, chunk_document
from ingestion.documents import SourceDocument, SourceType
from ingestion.index import Chunker, record_run, sync_documents
from ingestion.sources.code_travail import (
    CleanSummary,
    clean_code_travail,
    download_code_travail,
    load_code_travail,
    snapshot_dir,
)
from ingestion.sources.legifrance import LegifranceClient

logger = logging.getLogger("ingestion")


@dataclass(frozen=True)
class Source:
    source_types: tuple[SourceType, ...]
    snapshot: Callable[[Path], Path]  # data_dir -> snapshot directory
    clean: Callable[[Path], CleanSummary]  # raw files -> cleaned files, in the snapshot
    load: Callable[[Path], list[SourceDocument]]  # cleaned files -> documents


SOURCES: dict[str, Source] = {
    "articles": Source(
        ("article",), snapshot=snapshot_dir, clean=clean_code_travail, load=load_code_travail
    ),
    # "fiches": added with the fiches loaders.
}


def print_report(documents: Sequence[SourceDocument], chunker: Chunker) -> None:
    """Articles per part and title, plus chunk statistics (phase 1 exit criterion)."""
    by_title = Counter(
        " > ".join((d.hierarchy[0], d.hierarchy[3])) if len(d.hierarchy) > 3 else d.source_type
        for d in documents
    )
    print(f"\n{len(documents)} documents")
    for key, count in sorted(by_title.items()):
        print(f"  {count:5d}  {key}")

    chunk_lists = [chunker(d) for d in documents]
    tokens = sorted(approx_token_count(c.content) for chunks in chunk_lists for c in chunks)
    split = sum(1 for chunks in chunk_lists if len(chunks) > 1)
    if tokens:
        print(
            f"{len(tokens)} chunks ({split} documents split) — approx. tokens per chunk: "
            f"median {tokens[len(tokens) // 2]}, max {tokens[-1]}\n"
        )


async def fetch(on: date, force: bool) -> int:
    settings = get_settings()
    dest = snapshot_dir(settings.data_dir)
    async with LegifranceClient.from_settings(settings) as client:
        summary = await download_code_travail(client, dest, on=on, force=force)
    print(
        f"{summary.total} articles in force on {on} -> {dest} "
        f"(downloaded={summary.downloaded} reused={summary.reused} removed={summary.removed})"
    )
    return clean()


def clean() -> int:
    data_dir = get_settings().data_dir
    for name, source in SOURCES.items():
        snapshot = source.snapshot(data_dir)
        summary = source.clean(snapshot)
        print(f"{name}: {summary.written} cleaned files in {snapshot} (removed={summary.removed})")
    return 0


async def run(only: str | None, dry_run: bool) -> int:
    settings = get_settings()
    started_at = datetime.now(UTC)
    chunker: Chunker = partial(chunk_document, max_tokens=settings.chunk_max_tokens)
    selected = {only: SOURCES[only]} if only else SOURCES

    documents: list[SourceDocument] = []
    source_types: list[SourceType] = []
    for name, source in selected.items():
        src = source.snapshot(settings.data_dir)
        manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
        logger.info(
            "Loading %s from %s (in force on %s, fetched at %s)",
            name,
            src,
            manifest["in_force_on"],
            manifest["fetched_at"],
        )
        documents.extend(source.load(src))
        source_types.extend(source.source_types)
    print_report(documents, chunker)

    try:
        async with get_sessionmaker()() as session, session.begin():
            stats = await sync_documents(
                session, documents, source_types=source_types, chunker=chunker
            )
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

    fetch_parser = commands.add_parser("fetch", help="download sources into the data directory")
    fetch_parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="version of the texts in force on this date (default: today)",
    )
    fetch_parser.add_argument(
        "--force", action="store_true", help="re-download articles already on disk"
    )

    commands.add_parser("clean", help="rebuild the cleaned files from the raw files")

    run_parser = commands.add_parser("run", help="index the cleaned files into PostgreSQL")
    run_parser.add_argument("--dry-run", action="store_true", help="report changes, write nothing")
    run_parser.add_argument("--only", choices=sorted(SOURCES), help="ingest a single source")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.command == "fetch":
        return asyncio.run(fetch(args.date or datetime.now(UTC).date(), args.force))
    if args.command == "clean":
        return clean()
    return asyncio.run(run(args.only, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
