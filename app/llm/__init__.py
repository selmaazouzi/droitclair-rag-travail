"""LLM clients behind one interface; ``create_llm_client`` picks the configured provider."""

from app.config import Settings
from app.llm.base import ChatMessage, LLMChunk, LLMClient, LLMError, Usage
from app.llm.ollama import OllamaClient
from app.llm.openrouter import OpenRouterClient

__all__ = ["ChatMessage", "LLMChunk", "LLMClient", "LLMError", "Usage", "create_llm_client"]


def create_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "ollama":
        return OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            num_ctx=settings.ollama_num_ctx,
            think=settings.ollama_think,
            timeout_s=settings.llm_timeout_s,
        )
    if settings.openrouter_api_key is None:
        raise LLMError("OPENROUTER_API_KEY is not set in .env (or use LLM_PROVIDER=ollama).")
    return OpenRouterClient(
        api_key=settings.openrouter_api_key.get_secret_value(),
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
        timeout_s=settings.llm_timeout_s,
    )
