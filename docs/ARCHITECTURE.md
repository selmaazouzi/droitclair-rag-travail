# Chatbot Code du travail — Document d'architecture

> Document de référence du projet. Toute décision qui s'en écarte doit être discutée et ce document mis à jour.

## 1. Contexte et objectifs

Assistant conversationnel qui répond aux questions des salariés sur le **contrat de travail** en droit français, en s'appuyant exclusivement sur des sources officielles (Code du travail, fiches pratiques publiques) et en **citant systématiquement ses sources**.

Le projet est avant tout une démonstration d'ingénierie **LLMOps de bout en bout** :

- RAG hybride (lexical + vectoriel) avec reranking ;
- évaluation chiffrée et automatisée en CI ;
- garde-fous adaptés au domaine juridique ;
- observabilité complète (traces, coûts, latence, feedback) ;
- gestion de la fraîcheur des données (ré-ingestion incrémentale).

**Principe directeur : chaque modification qui touche la qualité (prompt, modèle, chunking, retrieval) est mesurée sur le jeu de test avant d'être adoptée.**

## 2. Périmètre fonctionnel

### V1 — inclus

Code du travail, première partie, Livre II « Le contrat de travail », titres suivants :

| Thème | Articles législatifs | Sujets |
|---|---|---|
| Formation et exécution du contrat | L122x | Embauche, période d'essai, modification du contrat |
| Rupture du CDI | L123x | Licenciement (personnel, économique), préavis, indemnité légale, démission, rupture conventionnelle |
| CDD | L124x | Motifs de recours, durée, renouvellement, indemnité de fin de contrat, requalification |

Inclure aussi les articles réglementaires correspondants (R122x–R124x, D122x–D124x) : de nombreux détails pratiques s'y trouvent (ex. calcul de l'indemnité légale de licenciement).

Compléter avec les **fiches pratiques** officielles sur ces thèmes, qui couvrent en langage clair la jurisprudence établie (clause de non-concurrence, modification du contrat, démission, etc.).

### Hors périmètre V1

- Autres parties du Code du travail (durée du travail, salaire, congés, santé et sécurité, représentation du personnel…) : le bot doit les **reconnaître** et répondre qu'il ne les couvre pas encore, avec un lien vers une source officielle.
- Conventions collectives (base KALI) : le bot doit **signaler** qu'elles peuvent prévoir des dispositions plus favorables, sans les consulter.
- Jurisprudence brute (Judilibre).
- Intérim, portage salarial, détachement (titres L125x et suivants).
- Conseil personnalisé : le bot donne une information juridique générale, jamais une consultation.

## 3. Principes techniques

1. **Backend découplé de l'interface** : toute la logique vit derrière l'API FastAPI. L'interface (Chainlit en V1) ne fait qu'afficher.
2. **Configuration par variables d'environnement** (Pydantic Settings). Aucun secret dans le code ni dans le dépôt.
3. **Prompts versionnés** : chaque prompt est un fichier texte dans `app/prompts/`, avec un identifiant de version tracé dans Langfuse.
4. **Fournisseurs interchangeables** : LLM, embeddings et reranker derrière des interfaces (`Protocol`) pour pouvoir comparer les implémentations.
5. **Ingestion idempotente** : relancer l'ingestion ne crée pas de doublons ; seuls les documents dont le contenu a changé sont recalculés (empreinte SHA-256).
6. **Citations vérifiables** : toute affirmation juridique de la réponse renvoie à un passage effectivement fourni au modèle.
7. **Tout est mesuré** : chaque requête est tracée, chaque version est évaluée.

## 4. Stack technique

| Brique | Choix | Remarques |
|---|---|---|
| Langage | Python 3.12 | Gestion des dépendances avec `uv` |
| API | FastAPI + Pydantic v2 | Streaming en SSE |
| Base de données | PostgreSQL 16 + pgvector | Recherche plein texte native en configuration `french` + extension `unaccent` |
| Accès BDD | SQLAlchemy 2 (async) + psycopg 3, migrations Alembic | |
| Embeddings | `BAAI/bge-m3` via sentence-transformers | Multilingue, dimension 1024, exécutable sur CPU |
| Reranker | `BAAI/bge-reranker-v2-m3` | Cross-encoder multilingue |
| LLM | API Mistral par défaut ; Ollama en développement local | Derrière l'interface `LLMClient` |
| Interface | Chainlit | Next.js envisageable plus tard, branché sur la même API |
| Observabilité | Langfuse | Cloud (offre gratuite) ou auto-hébergé via le docker-compose officiel |
| Évaluation | RAGAS + LLM-as-judge + métriques maison | |
| Qualité de code | ruff, mypy, pytest | |
| CI/CD | GitHub Actions | Lint, tests, évaluation de régression, ré-ingestion planifiée |
| Conteneurs | Docker, docker-compose | |

Les versions exactes sont figées dans `pyproject.toml` / `uv.lock`.

## 5. Vue d'ensemble

```
                         INGESTION (hors ligne, planifiée)
 ┌──────────┐   ┌───────────────┐   ┌────────────┐   ┌──────────────────────┐
 │ Sources  │──▶│ Parsing +     │──▶│ Chunking   │──▶│ Indexation           │
 │ JSON/XML │   │ filtre        │   │ par article│   │ embeddings + tsvector│
 └──────────┘   │ + normalisation│  └────────────┘   └──────────┬───────────┘
                └───────────────┘                               │
                                                                ▼
                                                   ┌────────────────────────┐
                                                   │ PostgreSQL + pgvector  │
                                                   └────────────┬───────────┘
                         REQUÊTE (en ligne)                     │
 ┌──────────┐   ┌───────────────┐   ┌───────────────┐   ┌───────┴──────┐   ┌──────────────┐
 │ Chainlit │──▶│ FastAPI       │──▶│ Garde-fous    │──▶│ Retrieval    │──▶│ Reranking    │
 │          │◀──│ /chat (SSE)   │   │ + reformul.   │   │ hybride (RRF)│   │ cross-encoder│
 └──────────┘   └───────▲───────┘   └───────────────┘   └──────────────┘   └──────┬───────┘
                        │                                                         │
                        │           ┌───────────────────┐   ┌───────────────┐     │
                        └───────────│ Garde-fous sortie │◀──│ LLM (stream)  │◀────┘
                                    │ + vérif citations │   │ avec citations│
                                    └───────────────────┘   └───────────────┘

 TRANSVERSE : Langfuse (traces de chaque étape) · Évaluation en CI · Ré-ingestion hebdomadaire
```

## 6. Structure du dépôt

```
droitclair-rag-travail/
├── CLAUDE.md                    # local, non versionné (.gitignore)
├── README.md
├── pyproject.toml
├── .env.example
├── docs/
│   └── ARCHITECTURE.md          # ce document
├── ingestion/
│   ├── __init__.py
│   ├── cli.py                   # point d'entrée : python -m ingestion.cli run
│   ├── sources/
│   │   ├── code_travail.py      # chargement JSON Code du travail
│   │   └── fiches.py            # chargement fiches pratiques
│   ├── filters.py               # périmètre + articles en vigueur
│   ├── normalize.py             # normalisation des numéros d'articles
│   ├── chunking.py
│   └── index.py                 # embeddings + upsert Postgres
├── app/
│   ├── __init__.py
│   ├── main.py                  # création de l'app FastAPI
│   ├── config.py                # Pydantic Settings
│   ├── api/
│   │   ├── chat.py              # POST /chat (SSE)
│   │   ├── feedback.py          # POST /feedback
│   │   └── health.py
│   ├── pipeline.py              # orchestration d'une requête
│   ├── retrieval/
│   │   ├── lexical.py
│   │   ├── dense.py
│   │   └── fusion.py            # Reciprocal Rank Fusion
│   ├── rerank.py
│   ├── query_rewrite.py
│   ├── guardrails/
│   │   ├── input.py
│   │   └── output.py
│   ├── llm/
│   │   ├── base.py              # Protocol LLMClient
│   │   ├── mistral.py
│   │   └── ollama.py
│   ├── prompts/
│   │   ├── system_v1.txt
│   │   ├── rewrite_v1.txt
│   │   └── scope_classifier_v1.txt
│   ├── db/
│   │   ├── models.py
│   │   └── session.py
│   └── tracing.py               # intégration Langfuse
├── ui/
│   └── chainlit_app.py          # client de l'API, aucune logique métier
├── eval/
│   ├── golden_set.jsonl
│   ├── run_eval.py
│   ├── metrics.py               # métriques maison (citations, comportement)
│   └── reports/                 # résultats datés (JSON + Markdown)
├── infra/
│   ├── docker-compose.yml
│   ├── Dockerfile.api
│   ├── Dockerfile.ui
│   └── alembic/
├── tests/
│   ├── unit/
│   └── integration/
└── .github/workflows/
    ├── ci.yml                   # lint + tests
    ├── eval.yml                 # évaluation de régression sur PR
    └── reingest.yml             # ré-ingestion planifiée
```

## 7. Sources de données

| Source | Contenu | Accès |
|---|---|---|
| Code du travail (LEGI) | Articles consolidés, en JSON | **API Légifrance** (portail PISTE, environnement de production), texte `LEGITEXT000006072050` : `POST /consult/legi/tableMatieres` (sommaire, `nature=CODE`) puis `POST /consult/getArticle` par article. OAuth *client credentials* (`LEGIFRANCE_CLIENT_ID` / `LEGIFRANCE_CLIENT_SECRET`), inscription gratuite |
| Fiches « Vos droits et démarches » | Fiches vulgarisées service-public.fr, thème Travail | Jeu de données DILA sur data.gouv.fr (XML) ou dépôt `SocialGouv/fiches-vdd` |
| Fiches du ministère du Travail | Fiches pratiques travail-emploi | Dépôt GitHub `SocialGouv/fiches-travail-data` (JSON) |

Le dépôt `SocialGouv/legi-data` (copie JSON quotidienne du Code) a été écarté : c'est une extraction marquée « expérimentale, usage interne », sans garantie de maintenance ni de stabilité du format. L'environnement *sandbox* de PISTE n'est pas utilisé pour l'ingestion : ses données diffèrent de la production.

**Important pour le développement :**

- **Inspecter la structure réelle des fichiers avant d'écrire les parsers.** Charger un échantillon, afficher les clés et la profondeur de l'arbre, puis écrire le parser. Ne pas supposer les noms de champs.
- Le JSON du Code est un arbre (sections imbriquées contenant des articles). Le parser doit parcourir l'arbre récursivement pour reconstruire le **chemin hiérarchique** de chaque article (Partie > Livre > Titre > Chapitre > Section > Article).
- Ne retenir que les articles dont l'état est « en vigueur ».
- Licence Ouverte 2.0 : mentionner la source (DILA / Légifrance / service-public.fr) et la date de mise à jour dans l'interface et le README.

### 7.1 Constats d'inspection de l'API Légifrance (5 novembre 2026)

- **Sommaire** (`/consult/legi/tableMatieres`, ~19 Mo) : arbre récursif `sections[]` / `articles[]`, profondeur maximale 9, **sans le texte des articles** (`content` vide). Champs utiles : pour un article `id` (identifiant de version), `cid` (identifiant stable entre versions), `num`, `etat` (`VIGUEUR` ou `ABROGE`) ; pour une section `title`, `etat`, `dateModif` (permet de ne retélécharger que les sections modifiées).
- Le code contient aussi des parties « ancienne » et une partie « Annexes » : seules « Partie législative » et « Partie réglementaire » sont concernées, et le filtre `etat` exclut les articles abrogés.
- **Périmètre V1** : 686 articles en vigueur, tous de numéro distinct.

  | Titre (Livre II) | Législatif | Réglementaire |
  |---|---|---|
  | II — Formation et exécution du contrat | 175 | 87 |
  | III — Rupture du CDI | 221 | 133 |
  | IV — CDD | 59 | 11 |

- **Article** (`/consult/getArticle`, corps `{"id": <id de version>}`) : `texte` est du texte brut où les sauts d'alinéa sont perdus ; `texteHtml` sépare les alinéas par `<br/>` et sert donc au découpage par alinéa (9.3). `nota` contient des précisions d'entrée en vigueur. `context.titresTM` donne la hiérarchie de l'article.
- Les dates d'article (`dateDebut`, `dateFin`) sont en millisecondes depuis l'epoch ; `32472144000000` (2999-01-01) signifie « sans date de fin ».
- `documents.source_id` reçoit le `cid` de l'article : stable d'une version à l'autre, il permet à la ré-ingestion de détecter une **modification** plutôt qu'une suppression suivie d'un ajout.
- `GET /consult/ping` répond `pong` en `text/plain` et renvoie une erreur 500 si la requête envoie `Accept: application/json`.
- Débit : l'extraction de SocialGouv se limite à environ 20 requêtes par seconde avec relances automatiques. On adopte la même prudence (environ 700 appels pour le périmètre, soit moins d'une minute).

## 8. Modèle de données

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Configuration plein texte française insensible aux accents
CREATE TEXT SEARCH CONFIGURATION french_unaccent (COPY = french);
ALTER TEXT SEARCH CONFIGURATION french_unaccent
  ALTER MAPPING FOR hword, hword_part, word WITH unaccent, french_stem;

CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type     TEXT NOT NULL,            -- 'article' | 'fiche_sp' | 'fiche_travail'
    source_id       TEXT NOT NULL UNIQUE,     -- ex. LEGIARTI..., identifiant de fiche
    article_num     TEXT,                     -- normalisé, ex. 'L1237-19' (NULL pour les fiches)
    title           TEXT NOT NULL,
    hierarchy_path  TEXT NOT NULL,            -- ex. 'Partie législative > ... > Article L1237-19'
    url             TEXT NOT NULL,
    valid_from      DATE,                     -- début de vigueur (articles)
    source_updated  DATE,                     -- date de mise à jour (fiches)
    content_hash    TEXT NOT NULL,            -- SHA-256 du contenu normalisé
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON documents (article_num);

CREATE TABLE chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INT NOT NULL,
    content         TEXT NOT NULL,            -- texte avec en-tête hiérarchique
    embedding       vector(1024),             -- NULL tant que non calculé (§9.4)
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('french_unaccent', content)) STORED,
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON chunks USING gin (tsv);

CREATE TABLE conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,            -- 'user' | 'assistant'
    content         TEXT NOT NULL,
    trace_id        TEXT,                     -- identifiant de trace Langfuse
    sources         JSONB,                    -- citations renvoyées
    feedback        SMALLINT,                 -- 1, -1 ou NULL
    feedback_comment TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ingestion_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    docs_added      INT, docs_updated INT, docs_removed INT, docs_unchanged INT,
    index_version   TEXT                      -- tracé dans Langfuse avec chaque requête
);
```

Le schéma est créé via des migrations Alembic, pas à la main.

## 9. Pipeline d'ingestion

Commande : `python -m ingestion.cli run [--dry-run] [--only articles|fiches] [--date AAAA-MM-JJ]`

`--dry-run` exécute toute la chaîne, y compris la comparaison avec la base, dans une transaction annulée à la fin : on voit ce qui changerait sans rien écrire. `--date` choisit la version des textes en vigueur à cette date (par défaut : aujourd'hui).

### 9.1 Filtrage du périmètre

```python
SCOPE_PATTERN = re.compile(r"^[LRD]12[2-4]\d")  # appliqué au numéro normalisé
```

Un article est retenu si son numéro normalisé correspond au motif **et** s'il est en vigueur. Les fiches sont retenues par thème (contrat de travail, période d'essai, CDD, rupture, démission, licenciement, rupture conventionnelle) ; la liste des thèmes est dans la configuration.

### 9.2 Normalisation des numéros d'articles

Fonction pure `normalize_article_ref(text: str) -> str | None`, utilisée **à l'ingestion et sur les requêtes utilisateur**.

| Entrée | Sortie |
|---|---|
| `L. 1237-19` | `L1237-19` |
| `l1237-19` | `L1237-19` |
| `article L 1237-19` | `L1237-19` |
| `R.1234-2` | `R1234-2` |
| `L1237-19-1` | `L1237-19-1` |
| `1237-19` (sans lettre) | `None` en sortie stricte ; une fonction séparée `candidate_refs()` renvoie `['L1237-19', 'R1237-19', 'D1237-19']` |

Prévoir aussi `extract_article_refs(text: str) -> list[str]` qui trouve toutes les références dans un texte libre. Couverture de tests unitaires exhaustive.

### 9.3 Chunking

- **Articles** : un article = un chunk. Si un article dépasse ~800 tokens, découpage par alinéa avec un chevauchement d'un alinéa.
- **Fiches** : découpage par section (titres de la fiche), cible 300 à 600 tokens.
- **Chaque chunk commence par un en-tête** :

```
[Code du travail — Article L1237-19]
Partie législative > Première partie : Les relations individuelles de travail > Livre II : Le contrat de travail > Titre III : Rupture du contrat de travail à durée indéterminée > Chapitre VII : Autres cas de rupture > Section 4 : ...
<un alinéa par ligne>
NOTA : <note de l'article, s'il y en a une>
```

- L'en-tête reprend les **titres complets** des sections : ils portent du sens (« Rupture d'un commun accord… ») utile aux recherches lexicale et vectorielle. À réévaluer en phase 4 si le bruit l'emporte.
- La **note** (`nota`) de l'article est incluse : elle précise souvent les conditions d'entrée en vigueur.
- Le tableau de L1235-3 (barème des indemnités) est restitué ligne par ligne, cellules séparées par « | ».
- Le nombre de tokens est **estimé** (3,5 caractères par token) tant que le tokenizer de `bge-m3` n'est pas branché (phase 3). Résultat au 5 novembre 2026 : 686 articles → 696 chunks, 9 articles découpés, médiane d'environ 250 tokens par chunk.

### 9.4 Indexation incrémentale

1. Calculer le `content_hash` de chaque document.
2. Comparer avec la base : nouveau → insérer ; hash différent → remplacer document et chunks ; absent de la source → supprimer.
3. Les chunks nouveaux ou modifiés sont enregistrés avec un embedding NULL ; l'étape d'embedding ne calcule que ceux-là.
4. Enregistrer les compteurs dans `ingestion_runs` et générer un `index_version`. L'`index_version` est une empreinte de l'ensemble `(source_id, content_hash)` : un contenu identique donne la même version.
5. **Garde-fou** : si une source renvoie moins de la moitié des documents déjà indexés pour son type, l'ingestion s'arrête sans rien supprimer (une panne de l'API ne doit pas vider l'index).

## 10. Pipeline de requête

Orchestré par `app/pipeline.py`. Chaque étape est une fonction tracée comme un *span* Langfuse.

| # | Étape | Détail | Paramètres par défaut |
|---|---|---|---|
| 1 | Garde-fous d'entrée | Masquage des données personnelles, détection de prompt injection, classification de périmètre | — |
| 2 | Reformulation | Réécriture en question autonome à partir des 4 derniers échanges ; extraction des références d'articles | Petit modèle, température 0 |
| 3 | Recherche lexicale | `ts_rank` sur `tsv` avec `websearch_to_tsquery('french_unaccent', ...)` ; si des références d'articles sont détectées, les documents correspondants sont ajoutés en tête | top 30 |
| 4 | Recherche vectorielle | Similarité cosinus pgvector | top 30 |
| 5 | Fusion | Reciprocal Rank Fusion : `score = Σ 1 / (k + rang)` | k = 60, garder 30 |
| 6 | Reranking | Cross-encoder sur les paires (question, chunk) | garder 6 |
| 7 | Génération | Prompt système + passages numérotés [1]…[6] + historique ; streaming | température 0,1 |
| 8 | Garde-fous de sortie | Vérification des citations, rappel conventions collectives, disclaimer | — |

### 10.1 Classification de périmètre (étape 1)

Appel LLM court avec sortie structurée (validée par Pydantic) :

```python
class ScopeDecision(BaseModel):
    category: Literal["in_scope", "adjacent_labor_law", "out_of_scope", "personal_dispute"]
    topic: str | None
    reason: str
```

- `in_scope` → pipeline normal.
- `adjacent_labor_law` (durée du travail, salaire, congés…) → réponse fixe : sujet non couvert, lien vers code.travail.gouv.fr ou service-public.fr.
- `out_of_scope` → réponse fixe polie.
- `personal_dispute` → pipeline normal **et** ajout obligatoire d'une redirection (inspection du travail, défenseur syndical, avocat, conseil de prud'hommes selon le cas).

### 10.2 Prompt système (version initiale `system_v1.txt`)

```
Tu es un assistant d'information juridique sur le contrat de travail en droit français.

Règles :
- Réponds uniquement à partir des passages fournis entre balises <sources>.
- Cite chaque affirmation avec le numéro du passage entre crochets, par exemple [2].
- Si les passages ne permettent pas de répondre, dis-le clairement et n'invente rien.
- Mentionne le numéro d'article quand la réponse repose sur un article du Code.
- Quand le sujet s'y prête (préavis, période d'essai, indemnités), précise que la
  convention collective ou le contrat peut prévoir des dispositions plus favorables.
- Tu donnes une information générale, pas un conseil juridique personnalisé.
- Réponds en français, de façon claire et concise, sans jargon inutile.
```

### 10.3 Vérification des citations (étape 8)

- Extraire les marqueurs `[n]` de la réponse.
- Tout marqueur hors de l'intervalle des passages fournis est signalé (métrique `invalid_citation_rate`) et retiré.
- Une réponse substantielle sans aucune citation est signalée (`uncited_answer`), tracée dans Langfuse.
- Les sources effectivement citées sont renvoyées au client avec titre, numéro d'article et URL.

## 11. API

### `POST /chat` — réponse en Server-Sent Events

Requête :

```json
{ "conversation_id": "uuid ou null", "message": "Quelle est la durée maximale d'une période d'essai pour un cadre ?" }
```

Événements SSE émis, dans l'ordre :

| Événement | Contenu |
|---|---|
| `meta` | `{ "conversation_id": "...", "message_id": "...", "trace_id": "..." }` |
| `token` | `{ "text": "..." }` (répété) |
| `sources` | `[{ "ref": 1, "title": "...", "article_num": "L1221-19", "url": "...", "updated": "..." }]` |
| `notice` | Disclaimer ou redirection éventuelle |
| `done` | `{ "latency_ms": ..., "usage": {...} }` |
| `error` | `{ "code": "...", "message": "..." }` |

### `POST /feedback`

```json
{ "message_id": "uuid", "score": 1, "comment": "optionnel" }
```

Enregistre le feedback en base et l'envoie comme *score* sur la trace Langfuse correspondante.

### `GET /health`

Vérifie la connexion à la base et la disponibilité du modèle d'embedding.

## 12. Garde-fous

| Garde-fou | Implémentation V1 |
|---|---|
| Données personnelles | Masquage par expressions régulières (e-mails, téléphones, numéros de sécurité sociale, IBAN) avant tout envoi au LLM et avant traçage |
| Prompt injection | Heuristiques simples (motifs connus) + consigne système ; les tentatives sont tracées |
| Périmètre | Classifieur de la section 10.1 |
| Litiges personnels | Redirection obligatoire ajoutée à la réponse |
| Conventions collectives | Consigne système + vérification en sortie sur les thèmes concernés |
| Hallucination | Vérification des citations (10.3) ; métrique faithfulness suivie en évaluation |
| Disclaimer | Affiché en permanence dans l'interface et ajouté via l'événement `notice` |
| Abus | Limitation de débit par IP sur `/chat` |

## 13. Observabilité

- Une **trace Langfuse par requête**, un **span par étape** du pipeline.
- Métadonnées systématiques sur la trace : `prompt_version`, `llm_model`, `embedding_model`, `reranker_model`, `index_version`, `scope_category`.
- Sur les spans de retrieval : identifiants et scores des chunks à chaque étape (lexical, dense, fusion, rerank), pour pouvoir diagnostiquer une mauvaise réponse.
- Usage de tokens et coût sur les appels LLM.
- Feedback utilisateur rattaché comme score à la trace.
- Les données personnelles sont masquées **avant** l'envoi des traces.

## 14. Évaluation

### 14.1 Jeu de test (`eval/golden_set.jsonl`)

Une ligne par question :

```json
{
  "id": "q001",
  "question": "Combien de fois peut-on renouveler un CDD ?",
  "category": "cdd",
  "expected_behavior": "answer",
  "expected_answer": "Deux fois, sauf disposition conventionnelle contraire, dans la limite de la durée maximale.",
  "expected_sources": ["L1243-13"]
}
```

`expected_behavior` ∈ `answer`, `answer_with_cc_reminder`, `redirect_personal`, `refuse_adjacent`, `refuse_out_of_scope`, `dont_know`.

Répartition cible (~100 questions) : période d'essai 15 %, CDD 20 %, rupture conventionnelle 15 %, licenciement et démission 20 %, références d'articles en formats variés 10 %, hors périmètre ou adjacent 10 %, litiges personnels 10 %.

**Les questions sont rédigées et validées par le porteur du projet, pas générées automatiquement.**

### 14.2 Métriques

| Métrique | Outil | Mesure |
|---|---|---|
| Faithfulness | RAGAS | La réponse est-elle fidèle aux passages ? |
| Answer relevancy | RAGAS | La réponse traite-t-elle la question ? |
| Context precision / recall | RAGAS | Le retrieval ramène-t-il les bons passages ? |
| Source hit rate @k | Maison | Les `expected_sources` sont-elles dans les k passages après reranking ? |
| Citation validity | Maison | Taux de citations valides |
| Behavior accuracy | Maison (LLM-as-judge pour les cas ambigus) | Le comportement attendu est-il respecté ? |
| Latence p50 / p95, coût moyen | Maison | Par requête |

### 14.3 Exécution

- `python -m eval.run_eval --config <nom>` produit `eval/reports/<date>_<config>.json` et un résumé Markdown.
- En CI (`eval.yml`) : sur chaque PR touchant `app/`, `ingestion/` ou `app/prompts/`, comparaison avec le dernier rapport de `main`. **La PR échoue si une métrique principale baisse de plus de 3 points.**
- Chaque amélioration de la phase 4 (hybride, rerank, reformulation) fait l'objet d'un rapport séparé, pour constituer le tableau de résultats du README.

## 15. Configuration

`.env.example` :

```
DATABASE_URL=postgresql+psycopg://chatbot:chatbot@localhost:5433/chatbot
LEGIFRANCE_API_URL=https://api.piste.gouv.fr/dila/legifrance/lf-engine-app
LEGIFRANCE_OAUTH_URL=https://oauth.piste.gouv.fr
LEGIFRANCE_CLIENT_ID=
LEGIFRANCE_CLIENT_SECRET=
LLM_PROVIDER=mistral            # mistral | ollama
LLM_MODEL=mistral-small-latest
LLM_REWRITE_MODEL=mistral-small-latest
MISTRAL_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434
EMBEDDING_MODEL=BAAI/bge-m3
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RETRIEVAL_TOP_K_LEXICAL=30
RETRIEVAL_TOP_K_DENSE=30
RRF_K=60
RERANK_TOP_N=6
PROMPT_SYSTEM_VERSION=system_v1
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=
RATE_LIMIT_PER_MINUTE=20
```

Chaque étape du pipeline doit pouvoir être désactivée par configuration (`ENABLE_RERANK`, `ENABLE_LEXICAL`, `ENABLE_QUERY_REWRITE`) pour les évaluations comparatives.

## 16. Tests

- **Unitaires** : normalisation et extraction des références, filtrage du périmètre, chunking (en-têtes, découpage par alinéa), calcul d'empreinte, fusion RRF, vérification des citations, masquage des données personnelles, parsing des événements SSE.
- **Intégration** : ingestion sur un petit échantillon fixe de documents (fixtures) dans une base Postgres de test, puis requête de bout en bout avec un LLM simulé.
- Les tests ne doivent jamais appeler d'API payante ; les clients LLM sont simulés.

## 17. Phases de développement

Ne passer à la phase suivante que lorsque le critère de fin est atteint.

| Phase | Contenu | Critère de fin |
|---|---|---|
| 0. Mise en place | Dépôt, `uv`, ruff, mypy, pytest, docker-compose (Postgres + pgvector), Alembic, CI lint + tests | `docker compose up` démarre la base ; la CI passe |
| 1. Données | Inspection des sources, parsers, filtrage, normalisation, chunking, indexation incrémentale | Rapport du nombre d'articles par titre ; une requête SQL retrouve L1237-19 ; relancer l'ingestion ne modifie rien |
| 2. Jeu de test | Format JSONL, script de validation du fichier | ~100 questions validées et versionnées |
| 3. Version minimale | Recherche vectorielle seule, prompt v1, `/chat` en SSE, Chainlit, Langfuse de base, déploiement | Réponse correcte avec citation sur une URL publique ; premier rapport d'évaluation (référence) |
| 4. Qualité du retrieval | Lexical + RRF, puis rerank, puis reformulation | Un rapport d'évaluation par ajout, gains documentés |
| 5. Évaluation en CI | RAGAS, métriques maison, comparaison à `main` | La CI bloque une régression |
| 6. Garde-fous | Section 12 complète | > 90 % de bon comportement sur les catégories hors périmètre et litiges |
| 7. Observabilité et feedback | Métadonnées complètes, feedback relié aux traces, coûts | Depuis un feedback négatif, on retrouve toute la trace |
| 8. Fraîcheur et finitions | `reingest.yml` hebdomadaire, README (schéma, résultats, limites), démo | Une modification simulée d'un article est détectée et réindexée |

## 18. Conventions de code

- Code, noms de variables et docstrings en anglais ; textes affichés à l'utilisateur et prompts en français.
- Typage complet, `mypy --strict` sur `app/` et `ingestion/`.
- Fonctions pures pour la logique (normalisation, filtrage, fusion) ; I/O isolées.
- Async de bout en bout dans l'API.
- Pas de logique métier dans `ui/`.
- Commits conventionnels (`feat:`, `fix:`, `eval:`, `docs:`…).

## 19. Limites connues (à documenter dans le README)

- Pas de prise en compte des conventions collectives ni des accords d'entreprise.
- Pas de jurisprudence brute ; seule la jurisprudence résumée dans les fiches officielles est couverte.
- Périmètre limité au Livre II, titres II à IV.
- Information générale uniquement, pas de conseil personnalisé.

## 20. Évolutions envisagées (hors V1)

- Extension aux autres parties du Code du travail.
- Recherche BM25 native (extension `pg_search`) comparée à `ts_rank`.
- Outils de calcul (indemnité de licenciement, préavis) via tool calling, en réutilisant les règles publiées par Code du travail numérique.
- Prise en compte des conventions collectives (base KALI) avec sélection de la convention par l'utilisateur.
- Interface Next.js branchée sur la même API.
