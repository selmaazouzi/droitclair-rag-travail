from pathlib import Path

import pytest

from app.config import Settings


def test_defaults_match_architecture() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.llm_provider == "openrouter"
    assert settings.ollama_model == "gemma4:12b"
    assert settings.embedding_model == "BAAI/bge-m3"
    assert settings.reranker_model == "BAAI/bge-reranker-v2-m3"
    assert settings.retrieval_top_k_lexical == 30
    assert settings.retrieval_top_k_dense == 30
    assert settings.rrf_k == 60
    assert settings.rerank_top_n == 6
    assert settings.prompt_system_version == "system_v1"
    assert settings.enable_lexical and settings.enable_rerank and settings.enable_query_rewrite


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("ENABLE_RERANK", "false")
    monkeypatch.setenv("RERANK_TOP_N", "4")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.llm_provider == "ollama"
    assert settings.enable_rerank is False
    assert settings.rerank_top_n == 4


def test_secrets_are_not_leaked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-very-secret")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert "sk-very-secret" not in repr(settings)
    assert settings.openrouter_api_key is not None
    assert settings.openrouter_api_key.get_secret_value() == "sk-very-secret"


def test_rejects_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "unknown")

    with pytest.raises(ValueError, match="llm_provider"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_empty_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # .env.example ships keys with empty values, e.g. `CHUNK_MAX_TOKENS=`.
    monkeypatch.setenv("CHUNK_MAX_TOKENS", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.chunk_max_tokens is None
    assert settings.openrouter_api_key is None


def test_chunk_max_tokens_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHUNK_MAX_TOKENS", "800")
    assert Settings(_env_file=None).chunk_max_tokens == 800  # type: ignore[call-arg]


def test_hf_cache_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.embeddings import is_in_hf_cache

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    assert not is_in_hf_cache("BAAI/bge-m3")
    (tmp_path / "models--BAAI--bge-m3" / "snapshots" / "abc123").mkdir(parents=True)
    assert is_in_hf_cache("BAAI/bge-m3")
