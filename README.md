# droitclair-rag-travail

Assistant conversationnel qui répond aux questions des salariés sur le **contrat de travail** en droit français, en s'appuyant exclusivement sur des sources officielles (Code du travail, fiches pratiques publiques) et en **citant systématiquement ses sources**.

Le projet sert de démonstration d'ingénierie LLMOps de bout en bout : RAG hybride avec reranking, évaluation automatisée en CI, garde-fous propres au domaine juridique, observabilité, ré-ingestion incrémentale.

> ⚠️ Information juridique générale uniquement, pas de conseil personnalisé.

Architecture et décisions : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Rôle de chaque fichier de code : [docs/STRUCTURE.md](docs/STRUCTURE.md).

## État d'avancement

| Phase | Statut |
|---|---|
| 0. Mise en place (outillage, base de données, migrations, CI) | ✅ |
| 1. Données (ingestion du Code du travail et des fiches) | 🟡 articles du Code faits (sans embeddings) ; fiches à faire |
| 2 – 8 | — |

## Démarrage

Prérequis : [uv](https://docs.astral.sh/uv/) et Docker.

```bash
uv sync                                              # dépendances (Python 3.12)
cp .env.example .env                                 # puis renseigner les clés
docker compose -f infra/docker-compose.yml up -d     # PostgreSQL 16 + pgvector (port 5433)
uv run alembic upgrade head                          # schéma de la base
uv run uvicorn app.main:app --reload                 # API sur http://localhost:8000
curl localhost:8000/health
```

Ingestion des articles du Code du travail (identifiants PISTE requis dans `.env`) :

```bash
uv run python -m ingestion.cli fetch            # API -> data/.../articles_bruts/, puis nettoyage
uv run python -m ingestion.cli clean            # articles_bruts/ -> articles/ (nettoyés)
uv run python -m ingestion.cli run --dry-run    # affiche ce qui changerait, sans écrire
uv run python -m ingestion.cli run              # articles/ -> PostgreSQL
uv run python -m ingestion.cli embed            # vecteurs bge-m3 (modèle local, ~2 Go au 1er lancement)
```

L'API peut aussi tourner dans Docker :

```bash
docker compose -f infra/docker-compose.yml --profile api up -d --build
```

## Tester le chatbot à la main

LLM local avec [Ollama](https://ollama.com) (`brew install ollama && brew services start ollama`, puis `ollama pull gemma4:12b`) et, dans `.env`, `LLM_PROVIDER=ollama` et `LLM_MODEL=gemma4:12b`.

```bash
uv run python -m app.cli ask "Combien de fois peut-on renouveler un CDD ?"
uv run python -m app.cli chat --passages      # plusieurs questions, avec les passages trouvés
```

## Qualité

```bash
uv run ruff format . && uv run ruff check .
uv run mypy
uv run pytest                                        # tests unitaires
TEST_DATABASE_URL=postgresql+psycopg://chatbot:chatbot@localhost:5433/chatbot_test \
  uv run pytest                                      # + tests d'intégration (base effacée)
```

## Sources et licence des données

Les données proviennent de Légifrance (DILA), service-public.fr et du ministère du Travail, réutilisées sous [Licence Ouverte 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence/). La date de mise à jour des sources sera affichée dans l'interface.
