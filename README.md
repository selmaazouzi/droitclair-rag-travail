<div align="center">

<img src="ui/public/favicon.svg" width="72" alt="Logo DroitClair">

# DroitClair

**Un assistant qui répond aux questions des salariés sur le contrat de travail, à partir du Code du travail officiel, en citant ses sources.**

[![CI](https://github.com/selmaazouzi/droitclair-rag-travail/actions/workflows/ci.yml/badge.svg)](https://github.com/selmaazouzi/droitclair-rag-travail/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![PostgreSQL + pgvector](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-4169E1?logo=postgresql&logoColor=white)
![Données : Licence Ouverte 2.0](https://img.shields.io/badge/donn%C3%A9es-Licence%20Ouverte%202.0-198754)

<img src="docs/images/periode_essai.png" width="820" alt="DroitClair répond à une question sur la période d'essai d'un cadre, avec les articles du Code du travail cités en sources">

</div>

> ⚠️ DroitClair donne une **information juridique générale**, pas un conseil personnalisé. La convention collective ou le contrat de travail peuvent prévoir des dispositions plus favorables.

## En bref

DroitClair est un assistant de type **RAG** (*Retrieval-Augmented Generation*) : avant de répondre, il retrouve les articles pertinents du Code du travail, puis demande à un modèle de langage de rédiger une réponse **uniquement à partir de ces articles**, chaque affirmation renvoyant à sa source.

- **Sources officielles uniquement** : les 686 articles en vigueur du Code du travail sur le contrat de travail, récupérés par l'API Légifrance de la DILA.
- **Réponses sourcées et vérifiées** : chaque affirmation porte un numéro de source `[n]` ; les citations qui ne renvoient à aucun article fourni sont retirées automatiquement, et les sources citées sont des liens vers Légifrance.
- **Transparence** : l'interface montre les articles retrouvés et leur score de similarité, le modèle utilisé, le temps de réponse et le coût de chaque question.
- **Réponse en direct**, mot à mot, grâce au streaming (Server-Sent Events).
- **Modèle de langage interchangeable** : un modèle hébergé via OpenRouter, ou un modèle local via Ollama, sans changer le code.

Le projet est aussi une démonstration d'**ingénierie LLMOps de bout en bout** : ingestion incrémentale et idempotente, choix techniques documentés et mesurables, tests sur une vraie base de données, intégration continue.

## Exemples

<table>
<tr>
<td width="50%" valign="top">

**Une question en langage courant**

La question ne contient ni « rétractation » ni le numéro de l'article : la recherche par le sens retrouve quand même l'article L1237-13.

<img src="docs/images/rupture_conventionnelle.png" alt="Réponse sur le délai de rétractation d'une rupture conventionnelle, citant l'article L1237-13">

</td>
<td width="50%" valign="top">

**Les articles donnés au modèle**

L'étape « 6 articles du Code trouvés » se déplie : on voit ce que le modèle a lu avant de répondre, et avec quel score.

<img src="docs/images/articles_trouves.png" alt="Liste des articles du Code du travail retrouvés pour la question, avec leur score de similarité">

</td>
</tr>
</table>

## Fonctionnement

```mermaid
flowchart LR
    subgraph ING["Ingestion (hors ligne)"]
        A["API Légifrance<br/>(PISTE)"] -->|fetch| B["Articles bruts<br/>(JSON)"]
        B -->|clean| C["Articles nettoyés"]
        C -->|run| D[("PostgreSQL<br/>+ pgvector")]
        D -->|"embed (bge-m3)"| D
    end
    subgraph REQ["Question d'un utilisateur"]
        U["Interface<br/>Chainlit"] -->|POST /chat| F["API FastAPI"]
        F -->|"6 articles les plus proches"| D
        F -->|"prompt + sources numérotées"| L["LLM<br/>OpenRouter / Ollama"]
        L -->|réponse en flux| F
        F -->|"SSE : texte, sources, coût"| U
    end
```

**Ingestion.** Le sommaire du Code du travail est téléchargé, filtré sur le périmètre (livre II : formation et exécution du contrat, rupture du CDI, CDD ; articles législatifs L et réglementaires R/D en vigueur), puis chaque article est récupéré, converti du HTML en alinéas propres et enregistré avec sa position dans le code. Une empreinte SHA-256 par article rend l'ingestion **idempotente** : une relance ne modifie rien, une nouvelle version d'article est détectée et seule elle est retraitée, y compris son vecteur.

**Réponse à une question.** La question est transformée en vecteur par le même modèle que les articles (`bge-m3`), les 6 articles les plus proches sont retrouvés par similarité cosinus (index HNSW de pgvector), puis transmis au modèle de langage, numérotés, avec un prompt versionné qui lui impose de ne répondre qu'à partir de ces sources. La réponse est diffusée en direct ; ses citations sont ensuite vérifiées, et la question comme la réponse sont enregistrées.

## Choix techniques

| Brique | Choix | Pourquoi |
|---|---|---|
| Source des données | API Légifrance (PISTE, DILA) | Source officielle de référence, structure complète du code, mises à jour détectables |
| Embeddings | `BAAI/bge-m3` (1 024 dimensions), en local | Multilingue, bon en français, accepte des textes longs (8 192 tokens) : un article = un passage, sans découpage |
| Base vectorielle | PostgreSQL 16 + pgvector | Une seule base pour les documents, la recherche vectorielle et la recherche plein texte (accents ignorés) |
| LLM hébergé | OpenRouter, `mistralai/mistral-small-3.2-24b-instruct` par défaut | Une clé pour de nombreux modèles, coût réel renvoyé à chaque appel |
| LLM local | Ollama, `gemma4:12b` | Choisi avec [llmfit](https://github.com/AlexsJones/llmfit) comme meilleur modèle officiel pour la machine de développement (Apple M5, 24 Go) |
| API | FastAPI, réponses en Server-Sent Events | Affichage mot à mot, événements typés (passages, texte, sources, statistiques) |
| Interface | Chainlit | Simple cliente de l'API : aucune logique métier côté interface |
| Qualité | uv, ruff, mypy `--strict`, pytest, GitHub Actions | Tests sur une vraie base pgvector ; modèles simulés en CI, aucun appel payant |

Le détail des décisions et de leurs justifications est dans [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quelques chiffres

| | |
|---|---|
| Articles indexés | **686** articles en vigueur (455 législatifs, 231 réglementaires), un passage par article |
| Instantané des textes | version en vigueur au 5 novembre 2026 |
| Temps de réponse observé | ~2 à 4 s avec `mistral-small-3.2` (OpenRouter) ; ~13 s avec `gemma4:12b` en local |
| Coût observé | de 0,00004 $ à 0,0002 $ par question avec `mistral-small-3.2` |
| Tests | 162 (133 unitaires, 29 d'intégration sur PostgreSQL) |

Ces chiffres de temps et de coût proviennent de tests manuels. L'**évaluation chiffrée** de la qualité des réponses, sur un jeu de questions de référence, est la prochaine étape du projet.

## Démarrage rapide

Prérequis : [uv](https://docs.astral.sh/uv/), Docker, des identifiants [PISTE](https://piste.gouv.fr) (API Légifrance) et une clé [OpenRouter](https://openrouter.ai/keys) ou [Ollama](https://ollama.com).

```bash
# 1. Installation et configuration
uv sync
cp .env.example .env            # renseigner LEGIFRANCE_CLIENT_ID / _SECRET et OPENROUTER_API_KEY

# 2. Base de données
docker compose -f infra/docker-compose.yml up -d
uv run alembic upgrade head

# 3. Données : téléchargement, indexation, vectorisation
uv run python -m ingestion.cli fetch
uv run python -m ingestion.cli run
uv run python -m ingestion.cli embed      # télécharge bge-m3 (~2 Go) la première fois
```

Puis, dans deux terminaux :

```bash
uv run uvicorn app.main:app --reload                       # API      → http://localhost:8000
cd ui && uv run chainlit run chainlit_app.py --port 8001    # interface → http://localhost:8001
```

Ou directement dans le terminal : `uv run python -m app.cli chat --passages`.

Pour utiliser un modèle local plutôt qu'OpenRouter : `brew install ollama && brew services start ollama && ollama pull gemma4:12b`, puis `LLM_PROVIDER=ollama` dans `.env`.

## Tests et qualité

```bash
uv run ruff format . && uv run ruff check .     # format et lint
uv run mypy                                     # typage strict
uv run pytest                                   # tests unitaires
TEST_DATABASE_URL=postgresql+psycopg://chatbot:chatbot@localhost:5433/chatbot_test \
  uv run pytest                                 # + tests d'intégration (base de test effacée)
```

La CI GitHub Actions exécute le lint, le typage et l'ensemble des tests sur une base PostgreSQL + pgvector, sans télécharger de modèle : l'embedding et le LLM sont simulés.

## Organisation du dépôt

```
app/         API FastAPI : configuration, recherche, clients LLM, prompts, garde-fous, /chat
ingestion/   chaîne d'ingestion : API Légifrance → data/ → PostgreSQL → vecteurs
ui/          interface Chainlit (thème, logo, traduction française)
infra/       Docker Compose, Dockerfile, migrations Alembic
tests/       tests unitaires et d'intégration
docs/        architecture, structure du code, captures d'écran
```

Le rôle de chaque fichier est décrit dans [docs/STRUCTURE.md](docs/STRUCTURE.md).

## Limites connues

- **Périmètre** : contrat de travail uniquement (livre II du Code du travail). Pas encore de durée du travail, de salaire ou de congés, ni de conventions collectives ou de jurisprudence.
- **Pas encore de garde-fous** : une question hors sujet n'est pas encore refusée explicitement.
- **Pas de mémoire de conversation** : chaque question est traitée indépendamment.
- **Recherche par le sens seulement** : la formulation de la question influe sur le classement des articles ; la recherche hybride et le reranking (phase 4) visent à le corriger.

## Sources et licence des données

Textes issus de **Légifrance** (DILA), réutilisés sous [Licence Ouverte 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence/). Chaque source citée renvoie à l'article sur Légifrance, avec sa date d'entrée en vigueur.
