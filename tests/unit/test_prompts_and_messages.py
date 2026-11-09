import uuid
from datetime import date

import pytest

from app.pipeline import build_messages
from app.prompts import Prompt, load_prompt
from app.retrieval.dense import RetrievedChunk


def passage(num: str, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        content=f"[Code du travail — Article {num}]\nPartie législative\n{text}",
        score=0.6,
        article_num=num,
        title=f"Article {num}",
        url="https://example.test",
        valid_from=date(2008, 5, 1),
        source_updated=None,
    )


def test_load_system_v1() -> None:
    prompt = load_prompt("system_v1")
    assert prompt.version == "system_v1"
    assert prompt.text.startswith("Tu es un assistant d'information juridique")
    assert "<sources>" in prompt.text and "[2]" in prompt.text


def test_unknown_prompt_lists_available_versions() -> None:
    with pytest.raises(FileNotFoundError, match="system_v1"):
        load_prompt("system_v99")


def test_build_messages_numbers_the_passages() -> None:
    messages = build_messages(
        Prompt("v", "SYSTEM"),
        "Combien de fois renouveler un CDD ?",
        [passage("L1243-13", "Texte A."), passage("L1243-13-1", "Texte B.")],
    )
    assert [m.role for m in messages] == ["system", "user"]
    assert messages[0].content == "SYSTEM"
    user = messages[1].content
    assert user.startswith("<sources>\n[1] [Code du travail — Article L1243-13]")
    assert "\n\n[2] [Code du travail — Article L1243-13-1]" in user
    assert user.endswith("</sources>\n\nQuestion : Combien de fois renouveler un CDD ?")
