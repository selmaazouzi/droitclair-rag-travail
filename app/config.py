"""Application settings, read from environment variables (and `.env` in development).

Every tunable of the pipeline lives here so that evaluation runs can compare
configurations without code changes (see docs/ARCHITECTURE.md §15).
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database ---
    database_url: str = "postgresql+psycopg://chatbot:chatbot@localhost:5433/chatbot"

    # --- Légifrance API (PISTE) — production by default; sandbox data differ from production ---
    legifrance_api_url: str = "https://api.piste.gouv.fr/dila/legifrance/lf-engine-app"
    legifrance_oauth_url: str = "https://oauth.piste.gouv.fr"
    legifrance_client_id: str | None = None
    legifrance_client_secret: SecretStr | None = None

    # --- LLM ---
    llm_provider: Literal["mistral", "ollama"] = "mistral"
    llm_model: str = "mistral-small-latest"
    llm_rewrite_model: str = "mistral-small-latest"
    mistral_api_key: SecretStr | None = None
    ollama_base_url: str = "http://localhost:11434"

    # --- Retrieval models ---
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # --- Retrieval parameters ---
    retrieval_top_k_lexical: int = Field(default=30, ge=1)
    retrieval_top_k_dense: int = Field(default=30, ge=1)
    rrf_k: int = Field(default=60, ge=1)
    rerank_top_n: int = Field(default=6, ge=1)

    # --- Pipeline switches (for comparative evaluations) ---
    enable_lexical: bool = True
    enable_rerank: bool = True
    enable_query_rewrite: bool = True

    # --- Prompts ---
    prompt_system_version: str = "system_v1"

    # --- Observability ---
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str | None = None

    # --- Abuse protection ---
    rate_limit_per_minute: int = Field(default=20, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
