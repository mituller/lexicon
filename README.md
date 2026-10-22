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

# General English from CEFR-J word lists (Bedrock-enriched JSONL)
#   --no-generate skips Bedrock for an offline dry run
python -m lexicon build-corpus --out ../data/seed/generated.jsonl --limit-per-level 60
python -m lexicon seed ../data/seed/generated.jsonl

# AI / technical domain from a curated term list (Bedrock assigns CEFR level + subfield)
python -m lexicon build-domain-corpus --terms ../data/seed/ai_terms.txt \
  --domain ai --out ../data/seed/ai_generated.jsonl
python -m lexicon seed ../data/seed/ai_generated.jsonl

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
