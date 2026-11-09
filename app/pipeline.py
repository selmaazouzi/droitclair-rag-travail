"""Answer one question (ARCHITECTURE §10), phase 3 version: dense retrieval only.

question -> embed -> top passages -> prompt with numbered sources -> streamed LLM
answer -> citation check. Not yet: input guardrails, query rewriting, lexical search,
fusion and reranking (phases 4 and 6), Langfuse tracing (phase 3, later).

Yields events so that callers (CLI now, the /chat SSE endpoint later) can stream.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import Embedder
from app.guardrails.output import (
    DISCLAIMER,
    CitationCheck,
    check_citations,
    remove_invalid_citations,
)
from app.llm import ChatMessage, LLMClient, Usage
from app.prompts import Prompt
from app.retrieval.dense import RetrievedChunk, dense_search


@dataclass(frozen=True, slots=True)
class Source:
    ref: int  # the [n] used in the answer
    title: str
    article_num: str | None
    url: str
    updated: date | None


@dataclass(frozen=True, slots=True)
class AnswerResult:
    answer: str  # invalid citations removed
    sources: tuple[Source, ...]  # passages actually cited
    passages: tuple[RetrievedChunk, ...]  # everything given to the model
    citations: CitationCheck
    notice: str
    usage: Usage | None
    latency_ms: int
    model: str
    prompt_version: str


@dataclass(frozen=True, slots=True)
class RetrievalDone:
    passages: tuple[RetrievedChunk, ...]


@dataclass(frozen=True, slots=True)
class Token:
    text: str


@dataclass(frozen=True, slots=True)
class Done:
    result: AnswerResult


PipelineEvent = RetrievalDone | Token | Done


def build_messages(
    prompt: Prompt, question: str, passages: Sequence[RetrievedChunk]
) -> list[ChatMessage]:
    """System prompt, then the numbered passages [1]..[n] and the question."""
    sources = "\n\n".join(f"[{i}] {p.content}" for i, p in enumerate(passages, start=1))
    user = f"<sources>\n{sources}\n</sources>\n\nQuestion : {question}"
    return [ChatMessage("system", prompt.text), ChatMessage("user", user)]


async def answer_question(
    question: str,
    *,
    session: AsyncSession,
    embedder: Embedder,
    llm: LLMClient,
    prompt: Prompt,
    top_k: int,
    temperature: float,
) -> AsyncIterator[PipelineEvent]:
    started = time.perf_counter()

    query_vector = await asyncio.to_thread(embedder.embed_query, question)
    passages = tuple(
        await dense_search(session, query_vector, model_name=embedder.model_name, top_k=top_k)
    )
    yield RetrievalDone(passages)

    parts: list[str] = []
    usage: Usage | None = None
    async for chunk in llm.stream(
        build_messages(prompt, question, passages), temperature=temperature
    ):
        if chunk.text:
            parts.append(chunk.text)
            yield Token(chunk.text)
        if chunk.usage is not None:
            usage = chunk.usage

    raw_answer = "".join(parts).strip()
    citations = check_citations(raw_answer, len(passages))
    sources = tuple(
        Source(
            ref=ref,
            title=passages[ref - 1].title,
            article_num=passages[ref - 1].article_num,
            url=passages[ref - 1].url,
            updated=passages[ref - 1].source_updated or passages[ref - 1].valid_from,
        )
        for ref in citations.cited
    )
    yield Done(
        AnswerResult(
            answer=remove_invalid_citations(raw_answer, len(passages)),
            sources=sources,
            passages=passages,
            citations=citations,
            notice=DISCLAIMER,
            usage=usage,
            latency_ms=round((time.perf_counter() - started) * 1000),
            model=llm.model,
            prompt_version=prompt.version,
        )
    )
