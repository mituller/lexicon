# Smoke-test checklist

Work top to bottom. Each stage lists the command, what a pass looks like, and how to fix a
failure. Stages 0–2 cost nothing; later stages spend a little AWS/Bedrock money.

Automate the read-only checks with:

```bash
python/.venv/bin/python scripts/smoke_test.py
```

It verifies identity, Bedrock access, the S3 Vectors bucket/index, the DynamoDB table, Cognito,
API health, and whether the corpus is seeded — without changing anything. Exit code is `0` when
nothing failed.

---

## Stage 0 — Local tooling

- [ ] `python3 --version` → 3.11+
- [ ] `node --version` → 20+ and `npm --version`
- [ ] `cargo --version` → 1.77+
- [ ] `python/.venv/bin/python -m pytest` → **43 passed**
- [ ] `cd python && ruff check . && mypy lexicon` → clean
- [ ] `cd apps/web && npm run build` → `built in ...`
- [ ] `cd apps/desktop/src-tauri && cargo clippy --all-targets -- -D warnings` → finished, no warnings

**Pass:** all three stacks green before touching AWS.

---

## Stage 1 — AWS credentials

```bash
aws configure --profile lexicon-admin      # or: aws configure sso --profile lexicon-admin
export AWS_PROFILE=lexicon-admin
export AWS_REGION=us-east-1
aws sts get-caller-identity
```

- [ ] Returns the **expected account id** and an `arn` for your admin user.

**If it fails:** `InvalidClientTokenId` → bad/expired keys; re-run `aws configure`.

> This identity is for deploying. The app itself uses Cognito + the CDK-created Lambda role.

---

## Stage 2 — Bedrock model access (separate from IAM!)

Console → **Bedrock** → region **us-east-1** → **Model access** → enable:

- [ ] `Amazon Titan Text Embeddings V2`
- [ ] `Amazon Nova Lite`

Verify with the script (makes one tiny embedding call):

```bash
python/.venv/bin/python scripts/smoke_test.py      # expect [PASS] Bedrock embedding call -> 1024 dims
```

**If it fails:** `AccessDeniedException` with valid IAM means the models aren't enabled yet.
IAM permissions and model access are **two independent gates**.

---

## Stage 3 — S3 Vectors bucket + index

```bash
python infra/bootstrap_s3vectors.py
```

- [ ] Prints `S3 Vectors ready: bucket=lexicon-vectors index=vocabulary dim=1024`

```bash
python/.venv/bin/python scripts/smoke_test.py      # expect [PASS] S3 Vectors bucket + index
```

**If it fails:** `AccessDenied` → admin policy missing `s3vectors:*`; service unavailable in
region → confirm **Vector buckets** appears in the S3 console.

---

## Stage 4 — Deploy the stack

```bash
cd infra/cdk
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cdk bootstrap aws://<ACCOUNT_ID>/us-east-1     # once per account/region
cdk deploy
```

- [ ] `cdk deploy` completes and prints outputs: `ApiUrl`, `UserPoolId`, `UserPoolClientId`,
      `CognitoHostedUiDomain`, `DynamoTableName`, `VectorBucket`, `VectorIndex`.

Save the outputs — Stage 5 needs them.

**If it fails:** `cdk bootstrap` errors → the deployer needs the broader permissions in
`docs/setup.md` §8 (or `AdministratorAccess`).

---

## Stage 5 — Wire local env

```bash
cp python/.env.example python/.env
cp apps/web/.env.example apps/web/.env
```

- [ ] `python/.env` has `LEXICON_COGNITO_USER_POOL_ID`, `LEXICON_COGNITO_CLIENT_ID`,
      `LEXICON_COGNITO_DOMAIN` from the CDK outputs.
- [ ] `apps/web/.env` has the matching `VITE_COGNITO_*` values.
- [ ] Desktop exports set: `LEXICON_API_BASE`, `LEXICON_COGNITO_DOMAIN`,
      `LEXICON_COGNITO_CLIENT_ID`, `LEXICON_COGNITO_CALLBACK_URL`.

```bash
export LEXICON_API_BASE='<ApiUrl>'
LEXICON_API_BASE='<ApiUrl>' python/.venv/bin/python scripts/smoke_test.py
```

- [ ] `[PASS] API health`, `[PASS] Cognito user pool`, `[PASS] DynamoDB table`.
- [ ] `[WARN] Corpus seeded` is expected here (index is empty until Stage 6).

**If API health fails:** the Function URL may need a moment after deploy; check the Lambda logs
in CloudWatch for import errors.

---

## Stage 6 — Build and ingest the corpus

Offline dry run first (no Bedrock spend):

```bash
cd python
python -m lexicon build-corpus --out /tmp/dryrun.jsonl --no-generate --limit-per-level 3
```

- [ ] Writes a handful of records; each has `term_id`, `cefr_level`, `difficulty_tier`,
      `domain`, `subfield`.

Then the real run (calls Bedrock once per word):

```bash
python -m lexicon build-corpus --out ../data/seed/generated.jsonl --limit-per-level 60
```

- [ ] Prints `parsed N CEFR-J entries` then `wrote N records`.
- [ ] Spot-check a few definitions/examples in the output file — they should be plain English
      appropriate to the CEFR level.

```bash
python -m lexicon seed ../data/seed/generated.jsonl
```

- [ ] Prints `embedded and upserted N records`.
- [ ] `python/.venv/bin/python ../scripts/smoke_test.py` now shows `[PASS] Corpus seeded`.

**If build-corpus fails:** `AccessDenied` → Stage 2 not done or IAM missing
`bedrock:InvokeModel`. **If seed fails:** Stage 3 not done, or the dimension is wrong (index is
1024; changing it requires a new index).

---

## Stage 7 — Retrieval and tutoring

```bash
cd python
python -m lexicon search "grounding a model in sources" --domain ai
python -m lexicon search "leave something behind permanently" --tier beginner
python -m lexicon ask "quiz me on advanced AI terms" --mode quiz
```

- [ ] `search` returns terms with sensible `[CEFR/tier] term — definition` lines.
- [ ] Filtering by `--domain ai` and `--tier beginner` visibly changes the result sets.
- [ ] `ask` returns a grounded quiz with an answer key, referencing retrieved terms.

**If search returns nothing:** re-check Stage 6 seeded (a filtered query can legitimately return
fewer than `top_k` on a small corpus).

---

## Stage 8 — Desktop app + login

```bash
cd apps/desktop
npm install
# with the Stage 5 exports set in this shell:
npm run dev
```

- [ ] The window opens and shows **Sign in** (not an error).
- [ ] Clicking **Sign in** opens the Cognito Hosted UI **in your browser**.
- [ ] After signing in with email, the app shows the stats bar and search panel.
- [ ] The user appears in **Cognito → User pool → Users**.
- [ ] Quit and relaunch → you are **still signed in** (keychain refresh worked).

**If login hangs:** the loopback port must match an allowed callback URL
(`http://127.0.0.1:4200/callback`). If it errors, add the new URL via
`-c extra_callback_urls='["http://127.0.0.1:4321/callback"]'`.

---

## Stage 9 — Google / GitHub login (optional, later)

Follow `docs/setup.md` §4–§5, then re-deploy:

```bash
cd infra/cdk
cdk deploy -c google_client_id='...apps.googleusercontent.com' -c google_secret_name='lexicon/google-oauth' \
           -c github_client_id='...' -c github_secret_name='lexicon/github-oauth'
```

- [ ] The Hosted UI shows Google and GitHub buttons.
- [ ] Signing in with each returns to the app and resolves to the **same `userId`** for the
      same email (check `GET /v1/me`).

---

## Stage 10 — After it works: tighten access

- [ ] Replace the deployer's `AdministratorAccess` with the scoped policy in `docs/setup.md` §8
      (or move deployment to a dedicated role).
- [ ] Confirm the app still works — it doesn't use this identity.
- [ ] Optional: delete the admin user's long-lived access keys if you've switched to SSO.

---

## Quick reference: what each failure usually means

| Symptom | Likely cause |
|---|---|
| `InvalidClientTokenId` | wrong/expired AWS credentials |
| `AccessDeniedException` on Bedrock | model access not enabled (Stage 2) |
| `AccessDeniedException` on `s3vectors:*` | bucket/index not created, or IAM missing the action |
| `NoCredentialsError` in local CLI | `AWS_PROFILE` not exported in that shell |
| Index empty after seed | wrong bucket/index name, or dimension mismatch |
| API 500 on `/health` | Lambda import error — read CloudWatch logs |
| Login hangs | callback URL/port mismatch, or Hosted UI domain typo |
| `state mismatch` on login | stale browser session — retry from a fresh sign-in |
