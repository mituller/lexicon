# lexicon

A vocabulary tutor that expands knowledge of the English language — build from CEFR-graded
levels or create your own vocabulary lists. RAG-backed via **Amazon S3 Vectors** + **Amazon
Bedrock**, with a React desktop app and (later) a web/PWA.

See [`prompt.md`](./prompt.md) for the full product spec and the locked architecture decisions.

## Architecture at a glance

- **Frontend:** React 19 + TypeScript + Vite + Zustand. Never calls AI/AWS directly.
- **Desktop:** thin Rust Tauri v2 shell — OS keychain, local SQLite cache, offline outbox.
- **Runtime + cloud API:** one Python package (`python/lexicon`) backing a FastAPI service on
  AWS Lambda (Function URL). Owns ingestion, retrieval, the tutor agent, progress, and lists.
- **AWS (`us-east-1`):** Cognito (auth), DynamoDB (progress), S3 Vectors (embeddings),
  Bedrock (Titan Text Embeddings V2 + Nova Lite).
- **Auth:** Cognito with Google + GitHub + email (Apple deferred — no custom domain needed).

### Why Python owns the runtime

Bedrock generation dominates latency, so Python's Lambda cold start is negligible, and `boto3`
is the reference SDK for S3 Vectors and Bedrock. Rust stays a thin shell so business logic is
easy to read and change. See `prompt.md` §1a (L1, L15–L19).

## How studying works

A session (`python/lexicon/session.py`) mixes due reviews with ~6 new words just above the
learner's **level**, a 0–100 estimate updated Elo-style from each word's first answer (with
multiple-choice guessing discounted). Exercises get harder as a word gets stronger: pick the
definition → pick the word → fill the blank (fresh Nova Pro sentence) → type the word. Wrong
options come from the word's S3 Vectors neighbours (synonyms excluded, same part of speech
first). A new word answered correctly on sight must be confirmed by typing before it counts as
known; a miss is rated *Again* once in FSRS and comes back three cards later as a different
exercise until it's right. Grading happens server-side; the answer key never reaches the client.

| Endpoint | Purpose |
|---|---|
| `POST /v1/session` | start a session → `session_id`, review/new counts, level |
| `GET /v1/session/{id}/next` | next exercise (no answer), or the summary when done |
| `POST /v1/session/{id}/answer` | `{exercise_id, choice \| text}` → correct?, full entry, next step |
| `POST /v1/session/{id}/explain` | why the chosen answer was wrong (generated once, cached) |
| `POST /v1/pool/prefetch` | grow the learner's next band; call without awaiting |

## Repo layout

```
apps/web/                 React 19 + TS + Vite + Zustand
apps/desktop/             Tauri v2 thin Rust shell
python/lexicon/           ingestion, retriever, tutor_agent, api, dynamo, srs, auth
python/tests/
infra/cdk/                AWS CDK (Python): Cognito, DynamoDB, Lambda, Function URL
infra/bootstrap_s3vectors.py  idempotent S3 Vectors bucket + index setup
data/                     seed corpus + schema docs
.github/workflows/        CI: python, web, rust
```

## Setup

See [`docs/setup.md`](./docs/setup.md) for the AWS (Bedrock model access, S3 Vectors, CDK
deploy, IAM permissions) and Google/GitHub OAuth steps, then work through
[`docs/smoke-test.md`](./docs/smoke-test.md).

Verify a deployed environment without changing anything:

```bash
python/.venv/bin/python scripts/smoke_test.py
```

## Quick start (local)

```bash
# Python
cd python
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check . && mypy lexicon

# Vocabulary grows on demand from online word lists (Octanove C1/C2 + NAWL):
#   1. fetch + score headwords into the DynamoDB candidate pool (no Bedrock)
python -m lexicon sync-sources
#      add your own list: --list gre=./gre.txt@80   (one word per line, 0-100 baseline)
#   2. enrich a difficulty band via Bedrock -> S3 Vectors + term index
#      (the API does this automatically when a learner's band runs low)
python -m lexicon refill --lo 60 --hi 70 --count 8
#      when a band has too few candidates, Bedrock suggests new words first
#      (verified against Datamuse); --no-suggest disables that
python -m lexicon suggest --lo 80 --hi 90 --count 15   # suggestions only, no enrichment
python -m lexicon pool    # terms vs pending candidates per band

# Search (filters work on both), and the RAG tutor
python -m lexicon search "grounding a model in sources" --domain ai --tier advanced
python -m lexicon ask "quiz me on advanced AI terms" --mode quiz --domain ai

# Web
cd apps/web
npm install
npm run dev

# Desktop (requires Rust + Tauri prerequisites)
cd apps/desktop
npm install
npm run tauri dev
```

## Status

Phases 0–4 code-complete (skeleton, auth, progress + offline store, corpus generation,
retrieval/tutor). Phase 6 hardening is partially done (scoped IAM, request validation, JSON
logging, data export + account deletion, keychain token storage). AWS provisioning is the
remaining gate — see `docs/setup.md` and `prompt.md` §3.
