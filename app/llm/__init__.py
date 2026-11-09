"""LLM clients behind one interface; ``create_llm_client`` picks the configured provider."""

from app.config import Settings
from app.llm.base import ChatMessage, LLMChunk, LLMClient, LLMError, Usage
from app.llm.mistral import MistralClient
from app.llm.ollama import OllamaClient

__all__ = ["ChatMessage", "LLMChunk", "LLMClient", "LLMError", "Usage", "create_llm_client"]


def create_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "ollama":
        return OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.llm_model,
            num_ctx=settings.ollama_num_ctx,
            think=settings.ollama_think,
            timeout_s=settings.llm_timeout_s,
        )
    if settings.mistral_api_key is None:
        raise LLMError("MISTRAL_API_KEY is not set (or use LLM_PROVIDER=ollama).")
    return MistralClient(
        api_key=settings.mistral_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )
