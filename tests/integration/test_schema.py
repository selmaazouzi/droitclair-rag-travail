"""The initial migration produces the schema described in docs/ARCHITECTURE.md §8."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.helpers import run_alembic

EXPECTED_TABLES = {"documents", "chunks", "conversations", "messages", "ingestion_runs"}


async def test_extensions_and_tables_exist(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        extensions = set((await conn.execute(text("SELECT extname FROM pg_extension"))).scalars())
        tables = set(
            (
                await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            ).scalars()
        )

    assert {"vector", "unaccent"} <= extensions
    assert tables >= EXPECTED_TABLES


async def test_french_full_text_is_accent_insensitive(engine: AsyncEngine) -> None:
    query = text(
        "SELECT to_tsvector('french_unaccent', :doc) @@ websearch_to_tsquery('french_unaccent', :q)"
    )
    async with engine.connect() as conn:
        accented_doc = await conn.scalar(
            query, {"doc": "La période d'essai du salarié", "q": "periode essai salarie"}
        )
        stemmed = await conn.scalar(
            query, {"doc": "Les licenciements économiques", "q": "licenciement économique"}
        )

    assert accented_doc is True
    assert stemmed is True


async def test_chunk_round_trip_with_lexical_and_dense_search(engine: AsyncEngine) -> None:
    embedding = "[" + ",".join(["0.1"] * 1024) + "]"
    async with engine.begin() as conn:
        doc_id = await conn.scalar(
            text(
                "INSERT INTO documents "
                "(source_type, source_id, article_num, title, hierarchy_path, url, content_hash) "
                "VALUES ('article', 'LEGIARTI-TEST', 'L1237-19', 'Article L1237-19', "
                "'Partie législative > Article L1237-19', 'https://example.test', 'abc') "
                "RETURNING id"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO chunks (document_id, chunk_index, content, embedding) "
                "VALUES (:doc_id, 0, 'Rupture conventionnelle collective', "
                "CAST(:embedding AS vector))"
            ),
            {"doc_id": doc_id, "embedding": embedding},
        )

        lexical_hit = await conn.scalar(
            text(
                "SELECT count(*) FROM chunks "
                "WHERE tsv @@ websearch_to_tsquery('french_unaccent', 'ruptures conventionnelles')"
            )
        )
        distance = await conn.scalar(
            text("SELECT embedding <=> CAST(:q AS vector) FROM chunks WHERE document_id = :d"),
            {"q": embedding, "d": doc_id},
        )

        await conn.execute(text("DELETE FROM documents WHERE id = :d"), {"d": doc_id})
        remaining = await conn.scalar(
            text("SELECT count(*) FROM chunks WHERE document_id = :d"), {"d": doc_id}
        )

    assert lexical_hit == 1
    assert distance is not None and abs(distance) < 1e-6
    assert remaining == 0, "chunks must be deleted with their document"


def test_orm_models_match_migrations(migrated_database: str) -> None:
    result = run_alembic(migrated_database, "check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_downgrade_then_upgrade(migrated_database: str) -> None:
    down = run_alembic(migrated_database, "downgrade", "base")
    assert down.returncode == 0, down.stderr
    up = run_alembic(migrated_database, "upgrade", "head")
    assert up.returncode == 0, up.stderr
