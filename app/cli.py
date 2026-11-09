"""Ask the assistant questions from the terminal, for manual testing.

    python -m app.cli ask "Combien de fois peut-on renouveler un CDD ?" [--passages]
    python -m app.cli chat [--passages]          # several questions in a row

Each question is answered independently (no conversation memory yet: query
rewriting comes in phase 4).
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from app.config import get_settings
from app.db.session import dispose_engine, get_sessionmaker
from app.embeddings import Embedder, SentenceTransformerEmbedder
from app.llm import LLMClient, LLMError, create_llm_client
from app.pipeline import AnswerResult, Done, RetrievalDone, Token, answer_question
from app.prompts import Prompt, load_prompt

DIM = "\033[2m"
BOLD = "\033[1m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _print_passages(result: AnswerResult) -> None:
    print(f"\n{BOLD}Passages donnés au modèle :{RESET}")
    for i, p in enumerate(result.passages, start=1):
        cited = "✓" if i in result.citations.cited else " "
        first_line = p.content.splitlines()[2] if len(p.content.splitlines()) > 2 else ""
        print(
            f"  {cited} [{i}] {p.title:20s} similarité {p.score:.3f}  {DIM}{first_line[:90]}{RESET}"
        )


def _print_footer(result: AnswerResult, show_passages: bool) -> None:
    print(f"\n\n{BOLD}Sources citées :{RESET}")
    if not result.sources:
        print("  (aucune)")
    for s in result.sources:
        updated = f" — en vigueur depuis le {s.updated:%d/%m/%Y}" if s.updated else ""
        print(f"  [{s.ref}] {s.title}{updated}\n      {DIM}{s.url}{RESET}")

    if result.citations.invalid:
        print(
            f"{YELLOW}⚠ Citations inexistantes retirées : {list(result.citations.invalid)}{RESET}"
        )
    if result.citations.uncited_answer:
        print(f"{YELLOW}⚠ Réponse sans aucune citation.{RESET}")
    if show_passages:
        _print_passages(result)

    tokens = ""
    if result.usage:
        u = result.usage
        tokens = f" | tokens {u.prompt_tokens} entrée / {u.completion_tokens} sortie"
    print(
        f"\n{DIM}{result.notice}\n"
        f"{result.model} | prompt {result.prompt_version} | "
        f"{result.latency_ms / 1000:.1f} s{tokens}{RESET}"
    )


async def _ask(
    question: str, embedder: Embedder, llm: LLMClient, prompt: Prompt, show_passages: bool
) -> None:
    settings = get_settings()
    async with get_sessionmaker()() as session:
        async for event in answer_question(
            question,
            session=session,
            embedder=embedder,
            llm=llm,
            prompt=prompt,
            top_k=settings.rerank_top_n,
            temperature=settings.llm_temperature,
        ):
            if isinstance(event, RetrievalDone):
                refs = ", ".join(p.article_num or p.title for p in event.passages)
                print(f"{DIM}Passages trouvés : {refs}{RESET}\n")
            elif isinstance(event, Token):
                print(event.text, end="", flush=True)
            elif isinstance(event, Done):
                _print_footer(event.result, show_passages)


async def main_async(questions: Sequence[str] | None, show_passages: bool) -> int:
    settings = get_settings()
    print(f"{DIM}Chargement de {settings.embedding_model}…{RESET}", flush=True)
    embedder = SentenceTransformerEmbedder(
        settings.embedding_model, device=settings.embedding_device
    )
    llm = create_llm_client(settings)
    prompt = load_prompt(settings.prompt_system_version)
    print(f"{DIM}LLM : {settings.llm_provider} / {llm.model}{RESET}\n")
    try:
        if questions is not None:
            for question in questions:
                await _ask(question, embedder, llm, prompt, show_passages)
            return 0
        while True:
            try:
                prompt_text = f"\n{BOLD}Question (vide pour quitter) > {RESET}"
                question = (await asyncio.to_thread(input, prompt_text)).strip()
            except EOFError:
                return 0
            if not question:
                return 0
            print()
            await _ask(question, embedder, llm, prompt, show_passages)
    except LLMError as exc:
        print(f"\n{YELLOW}Erreur LLM : {exc}{RESET}", file=sys.stderr)
        return 1
    finally:
        await llm.aclose()
        await dispose_engine()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="answer one question")
    ask.add_argument("question")
    chat = commands.add_parser("chat", help="ask several questions in a row")
    for sub in (ask, chat):
        sub.add_argument("--passages", action="store_true", help="show the retrieved passages")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    questions = [args.question] if args.command == "ask" else None
    return asyncio.run(main_async(questions, args.passages))


if __name__ == "__main__":
    sys.exit(main())
