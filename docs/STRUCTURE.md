# Structure du code

> Description de chaque fichier de code **existant**, tenue à jour à chaque création ou suppression de fichier.
> La structure **cible** du projet (fichiers prévus dans les phases suivantes) est décrite dans [ARCHITECTURE.md §6](ARCHITECTURE.md#6-structure-du-dépôt).

Dernière mise à jour : 9 novembre 2026 (route `POST /chat` en SSE et interface Chainlit).

## Vue d'ensemble

```
droitclair-rag-travail/
├── app/            API FastAPI (configuration, base de données, routes)
├── ingestion/      chaîne d'ingestion : API Légifrance -> data/ -> PostgreSQL
├── ui/             interface Chainlit, simple client de l'API
├── infra/          Docker, migrations Alembic
├── tests/          tests unitaires et d'intégration
├── docs/           documentation (ce fichier, ARCHITECTURE.md)
├── data/           instantanés téléchargés (non versionné)
└── .github/        intégration continue
```

## `app/` — API

| Fichier | Rôle |
|---|---|
| `app/__init__.py` | Marque le dossier comme package Python. |
| `app/cli.py` | Test manuel du chatbot : `python -m app.cli ask "question"` ou `chat` (plusieurs questions). Affiche la réponse en direct, les sources citées, les avertissements de citation, la latence et les tokens ; `--passages` montre les passages donnés au modèle. |
| `app/pipeline.py` | Orchestration d'une question (version phase 3) : vectorise la question, cherche les passages les plus proches, construit le prompt avec les sources numérotées, diffuse la réponse du LLM, vérifie les citations. Émet des événements (passages, morceaux de texte, résultat final) réutilisables par la future route `/chat`. |
| `app/main.py` | Crée l'application FastAPI (`create_app`), branche les routes (`/health`, `/chat`), ferme le pool de connexions à l'arrêt. Point d'entrée : `uvicorn app.main:app`. |
| `app/config.py` | Tous les réglages, lus depuis les variables d'environnement et `.env` (Pydantic Settings) : base de données, identifiants Légifrance, dossier `data/`, découpage, modèle d'embedding (nom, appareil, taille des lots), LLM, paramètres de recherche, Langfuse. Une variable vide (`KEY=`) vaut « non définie ». |
| `app/embeddings.py` | Interface `Embedder` (texte -> vecteur) et son implémentation locale `SentenceTransformerEmbedder` (`bge-m3`, 1024 dimensions, GPU Apple détecté automatiquement). Partagée par l'ingestion (documents) et, en phase 3, par l'API (questions). |
| `app/llm/__init__.py` | Point d'entrée des clients LLM : `create_llm_client` choisit OpenRouter ou Ollama selon `LLM_PROVIDER`. |
| `app/llm/base.py` | Interface commune `LLMClient` (réponse diffusée morceau par morceau) et types : message, morceau, consommation de tokens et coût, erreur. |
| `app/llm/ollama.py` | Client Ollama (modèle local, `/api/chat`) : fixe la taille de contexte, désactive par défaut la « réflexion » des modèles qui en font (`OLLAMA_THINK`), messages clairs si Ollama n'est pas lancé ou si le modèle n'est pas téléchargé. |
| `app/llm/openrouter.py` | Client OpenRouter (modèles hébergés, `/chat/completions`, flux SSE) : récupère tokens et coût de chaque appel, gère les erreurs survenant en cours de flux. |
| `app/prompts/__init__.py` | Chargement des prompts versionnés (`load_prompt("system_v1")`). |
| `app/prompts/system_v1.txt` | Prompt système v1 (ARCHITECTURE §10.2) : répondre uniquement à partir des sources, citer [n], ne rien inventer. |
| `app/retrieval/__init__.py` | Package de la recherche de passages. |
| `app/retrieval/dense.py` | Recherche vectorielle pgvector : les passages les plus proches de la question (similarité cosinus), seulement parmi ceux vectorisés par le même modèle. |
| `app/guardrails/__init__.py` | Package des garde-fous. |
| `app/guardrails/output.py` | Vérification des citations [n] (valides, inexistantes, réponse sans citation), retrait des citations inexistantes, texte d'avertissement. |
| `app/api/__init__.py` | Package des routes HTTP. |
| `app/api/chat.py` | `POST /chat` : répond à une question en flux SSE (événements `meta`, `passages`, `token`, `sources`, `notice`, `done`, `error`), enregistre question et réponse dans `conversations` / `messages`. Simple couche au-dessus de `app/pipeline.py` ; le modèle d'embedding est chargé à la première question. |
| `app/api/health.py` | `GET /health` : vérifie la connexion à la base, répond 200 ou 503. |
| `app/db/__init__.py` | Package base de données. |
| `app/db/models.py` | Modèles SQLAlchemy des tables `documents`, `chunks` (texte, vecteur et modèle qui l'a produit), `conversations`, `messages`, `ingestion_runs`. Doivent rester identiques aux migrations (vérifié par `alembic check` en test). |
| `app/db/session.py` | Moteur et sessions PostgreSQL asynchrones, créés au premier usage ; `get_session` fournit une session par requête HTTP. |

## `ingestion/` — chaîne d'ingestion

Quatre étapes : `fetch` (API -> `articles_bruts/`), `clean` (`articles_bruts/` -> `articles/`), `run` (`articles/` -> PostgreSQL), `embed` (chunks -> vecteurs).

| Fichier | Rôle |
|---|---|
| `ingestion/__init__.py` | Package de l'ingestion. |
| `ingestion/cli.py` | Commandes `python -m ingestion.cli fetch / clean / run / embed` : enchaîne les étapes, affiche le rapport (articles par titre, statistiques des chunks), gère `--dry-run`. |
| `ingestion/documents.py` | `SourceDocument` : format commun d'un document, quelle que soit la source. Calcule le chemin hiérarchique et l'empreinte du contenu ; se convertit vers et depuis le JSON des fichiers nettoyés (`to_dict` / `from_dict`). |
| `ingestion/normalize.py` | Normalisation des numéros d'articles (`"L. 1237-19"` -> `"L1237-19"`), extraction des références dans un texte libre, candidats L/R/D pour un numéro sans lettre. |
| `ingestion/filters.py` | Périmètre V1 : articles L, R, D 122x à 124x, en vigueur. |
| `ingestion/html_text.py` | Conversion du HTML Légifrance en alinéas de texte brut (paragraphes, sauts de ligne, liens, tableau ligne par ligne). |
| `ingestion/chunking.py` | Découpage en chunks avec en-tête (source, numéro, position dans le code) ; un article = un chunk par défaut, découpage par alinéa optionnel (`CHUNK_MAX_TOKENS`). |
| `ingestion/embed.py` | Calcule les vecteurs manquants : chunks sans embedding (nouveaux ou modifiés) ou produits par un autre modèle ; enregistre par lots, signale les chunks trop longs pour le modèle. |
| `ingestion/index.py` | Synchronisation incrémentale avec PostgreSQL : ajoute, met à jour ou supprime selon l'empreinte, enregistre chaque exécution dans `ingestion_runs`, refuse de vider l'index si la source rétrécit brutalement. |
| `ingestion/sources/__init__.py` | Package des sources. |
| `ingestion/sources/legifrance.py` | Client de l'API Légifrance (PISTE) : jeton OAuth, limitation du débit, relances automatiques ; routes `tableMatieres` (sommaire) et `getArticle`. |
| `ingestion/sources/code_travail.py` | Spécifique au Code du travail : parcours du sommaire, sélection du périmètre, lecture d'un article brut (`parse_article`), téléchargement de l'instantané (`fetch`), nettoyage (`clean`), chargement des articles nettoyés (`run`). |

## `ui/` — interface

| Fichier | Rôle |
|---|---|
| `ui/__init__.py` | Package de l'interface. |
| `ui/chainlit_app.py` | Interface Chainlit : envoie la question à `POST /chat`, affiche la réponse en direct, les articles trouvés (étape dépliable), les sources cliquables, l'avertissement, le modèle, la durée et le coût. Aucune logique métier. Lancement : `cd ui && uv run chainlit run chainlit_app.py --port 8001`. |
| `ui/sse_client.py` | Lecture d'un flux Server-Sent Events (lignes `event:` / `data:`), séparée de Chainlit pour être testée sans lui. |
| `ui/chainlit.md` | Page d'accueil affichée par Chainlit (présentation, avertissement juridique). |

## `infra/` — infrastructure

| Fichier | Rôle |
|---|---|
| `infra/docker-compose.yml` | Service `db` (PostgreSQL 16 + pgvector, port hôte 5433) et service `api` (profil `api`). |
| `infra/Dockerfile.api` | Image de l'API : dépendances via `uv`, migrations au démarrage, puis `uvicorn`. |
| `infra/initdb/01-create-test-db.sql` | Crée la base `chatbot_test` au premier démarrage du conteneur. |
| `infra/alembic/env.py` | Configuration d'Alembic : lit l'URL de la base dans les réglages, exécute les migrations en asynchrone. |
| `infra/alembic/script.py.mako` | Modèle utilisé pour générer une nouvelle migration. |
| `infra/alembic/versions/20261105_0001_initial_schema.py` | Migration 1 : extensions `vector` et `unaccent`, recherche plein texte `french_unaccent`, les 5 tables et leurs index. |
| `infra/alembic/versions/20261105_0002_nullable_embedding.py` | Migration 2 : `chunks.embedding` devient facultatif (rempli plus tard par l'étape d'embedding). |
| `infra/alembic/versions/20261109_0003_chunk_embedding_model.py` | Migration 3 : colonne `chunks.embedding_model`, le modèle qui a produit chaque vecteur. |

## `tests/`

| Fichier | Rôle |
|---|---|
| `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py` | Marqueurs de packages. |
| `tests/fakes.py` | Doublures de test partagées : `FakeEmbedder` (faux modèle d'embedding, sac de mots) et `FakeLLM` (réponse prédéfinie diffusée mot à mot), pour tester sans modèle ni API. |
| `tests/fixtures/legifrance/article_L1237-19.json` | Vraie réponse `getArticle` (champs réduits), utilisée sans appeler l'API. |
| `tests/unit/test_config.py` | Valeurs par défaut, surcharge par l'environnement, secrets masqués, variables vides. |
| `tests/unit/test_health.py` | `/health` avec une fausse session : base disponible (200) ou en panne (503). |
| `tests/unit/test_normalize.py` | Normalisation et extraction des numéros d'articles (formats variés, tirets typographiques, cas refusés). |
| `tests/unit/test_html_text.py` | Conversion HTML -> alinéas (paragraphes, `<br/>`, liens, tableau de L1235-3). |
| `tests/unit/test_documents_and_chunking.py` | Empreinte, conversion JSON, en-tête et découpage des chunks. |
| `tests/unit/test_code_travail.py` | Filtre du périmètre, parcours du sommaire, lecture d'un article réel, `fetch` / `clean` / chargement avec une fausse API. |
| `tests/unit/test_citations.py` | Détection des citations ([2], [1, 3], hors limites, texte entre crochets ignoré) et retrait des citations inexistantes. |
| `tests/unit/test_prompts_and_messages.py` | Chargement du prompt `system_v1` et construction du message avec les sources numérotées. |
| `tests/unit/test_llm_clients.py` | Clients Ollama et OpenRouter face à de faux serveurs : format du flux, tokens et coût, erreurs, Ollama non lancé ; choix du fournisseur. |
| `tests/unit/test_sse_client.py` | Lecture d'un flux SSE : événements nommés, commentaires, fins de ligne Windows, accents. |
| `tests/unit/test_legifrance_client.py` | Client API avec un faux serveur : jeton, relances, renouvellement après 401, erreurs. |
| `tests/integration/conftest.py` | Base de test réelle (`TEST_DATABASE_URL`, nom en `_test` obligatoire) : remise à zéro puis migrations. |
| `tests/integration/helpers.py` | Lance la commande Alembic contre la base de test. |
| `tests/integration/test_schema.py` | Schéma créé par les migrations : extensions, tables, recherche sans accents, recherche vectorielle, cascade, `alembic check`, aller-retour des migrations. |
| `tests/integration/test_embed.py` | Vectorisation avec le faux modèle : une seule fois par chunk, recalcul des seuls chunks modifiés, recalcul complet si le modèle change, recherche du plus proche voisin, dimension incorrecte refusée. |
| `tests/integration/test_chat_api.py` | `POST /chat` de bout en bout (base réelle, faux modèles) : ordre des événements, sources, enregistrement des messages, conversation réutilisée, 404/422, erreurs du LLM avant et pendant la réponse. |
| `tests/integration/test_pipeline.py` | Chaîne complète sur une vraie base avec faux modèles : passages trouvés, réponse diffusée, sources citées, citation inexistante retirée, réponse sans citation signalée. |
| `tests/integration/test_index.py` | Ingestion incrémentale : premier passage, relance sans effet, modification, suppression, garde-fou, changement de découpage. |

## Configuration du dépôt

| Fichier | Rôle |
|---|---|
| `pyproject.toml` | Dépendances (groupes `ml` : sentence-transformers et torch, `ui` : Chainlit ; installés en local, absents de la CI), version de Python, configuration de ruff, mypy, pytest et Alembic. |
| `uv.lock` | Versions exactes de toutes les dépendances (généré par `uv`). |
| `.env.example` | Modèle du fichier `.env` : toutes les variables, sans secret. |
| `.gitignore` | Fichiers exclus de git : `.env`, `data/`, caches, `CLAUDE.md`, `.claude/`. |
| `.dockerignore` | Fichiers exclus de l'image Docker. |
| `.github/workflows/ci.yml` | CI GitHub Actions : lint et types, puis tests sur une base pgvector, sans les groupes `ml` et `ui` (modèles simulés). |
