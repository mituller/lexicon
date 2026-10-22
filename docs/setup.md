# Setup guide

Everything you need to provision before the app runs against real AWS. Work top to bottom.

Region used throughout: **`us-east-1`**.

---

## 1. AWS: enable Bedrock model access (needed for Phase 3–4)

Bedrock models must be enabled per account **per region**.

1. AWS Console → **Bedrock** → make sure the region (top-right) is **US East (N. Virginia) us-east-1**.
2. Left nav → **Model access** → **Manage model access** (or **Modify model access**).
3. Enable:
   - `Amazon Titan Text Embeddings V2` (`amazon.titan-embed-text-v2:0`)
   - `Amazon Nova Lite` (`us.amazon.nova-lite-v1:0`)
4. Submit and wait until both show **Access granted** (usually seconds to a few minutes).

Also confirm **S3 Vectors** is available in your account/region (Console → S3 → **Vector buckets**).
If you don't see it, it may need to be enabled/requested for your account.

---

## 2. AWS: create the S3 Vectors bucket + index

Run once. Idempotent, safe to re-run.

```bash
python infra/bootstrap_s3vectors.py
```

Output: `S3 Vectors ready: bucket=lexicon-vectors index=vocabulary dim=1024`

> The index dimension, distance metric, and non-filterable keys are **immutable**. If you need to
> change them, create a new index (and re-embed).

---

## 3. AWS: deploy the CDK stack (Cognito + DynamoDB + Lambda)

### Prerequisites: Docker running + venv activated

**Docker Desktop must be running.** CDK builds the Lambda dependencies inside Docker (the
Python runtime needs Linux wheels for `numpy`, `pydantic-core`, and `cryptography`). Without it
you'll see:

```
CommandExecutionFailed: docker exited with status 1
```

The stack pins an arch-specific build image (`public.ecr.aws/sam/build-python3.12:latest-arm64`)
to match the function's **ARM64** architecture, so the bundled wheels are correct no matter
what CPU your machine has.

**Size limit:** Lambda rejects function bundles whose *unzipped* size exceeds 250 MB
(`Unzipped size must be smaller than 262144000 bytes`). The bundling command deliberately
copies only the `lexicon` package — never `.` — so the local `python/.venv` (hundreds of MB)
cannot leak into the artifact. After a synth you can check:

```bash
du -sh infra/cdk/cdk.out/asset.*        # ~130 MB expected
```

If you add heavy dependencies later and approach ~200 MB, drop `numpy` (only used to cast
vectors to float32; S3 Vectors casts server-side) or move deps into a Lambda layer.

**Also activate the venv before running `cdk`.** `cdk.json` runs `python3 app.py`, so without
it Python can't import `aws_cdk`:

```bash
cd /path/to/lexicon/infra/cdk
source .venv/bin/activate
```

### Credentials (use a dedicated profile)

Point the AWS CLI/CDK at the admin user with a named profile so you can't accidentally deploy
into the wrong account or leave ambient credentials around:

```bash
aws configure --profile lexicon-admin   # paste the access key id + secret
# (or, if your org uses IAM Identity Center: aws configure sso --profile lexicon-admin)

export AWS_PROFILE=lexicon-admin
export AWS_REGION=us-east-1
aws sts get-caller-identity             # confirm account + user before deploying
```

Keep these keys on your machine only. Never put them in the app, the desktop build, or the
Python `.env` — boto3 uses your AWS profile from the standard credential chain.

> ⚠️ **This identity is for deploying, not running.** The app authenticates *users* via Cognito,
> and reaches AWS through the **Lambda execution role** that CDK creates (already
> least-privilege). When you later reduce this user's access, that only affects deployment and
> local CLI runs (`build-corpus`, `seed`) — the app keeps working unchanged.

```bash
cd infra/cdk
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cdk bootstrap aws://<YOUR_ACCOUNT_ID>/us-east-1   # once per account/region
cdk deploy                      # production: data resources are retained
# cdk deploy -c dev=true        # development: data resources destroy on delete/rollback
```

> **Why `-c dev=true` exists:** with `RETAIN`, if a deploy fails and rolls back, the retained
> DynamoDB table / Cognito pool survive — and the next deploy fails with
> *"Resource of type ... already exists"*. The dev flag avoids that loop. It deletes user
> progress and accounts when the stack is destroyed, so don't use it for production.

Note the stack **outputs** — you'll need them for the app:

| Output | Where it goes |
|---|---|
| `ApiUrl` | `LEXICON_API_BASE` / `VITE_API_BASE` |
| `UserPoolId` | `LEXICON_COGNITO_USER_POOL_ID` |
| `UserPoolClientId` | `LEXICON_COGNITO_CLIENT_ID` / `VITE_COGNITO_CLIENT_ID` |
| `CognitoHostedUiDomain` | `LEXICON_COGNITO_DOMAIN` / `VITE_COGNITO_DOMAIN` |
| `CognitoIssuer` | (informational) |
| `DynamoTableName` | `LEXICON_DYNAMODB_TABLE` |

The Hosted UI domain looks like `lexicon-<account>.auth.us-east-1.amazoncognito.com`.

> Deploy works **before** creating Google/GitHub apps — the IdPs are optional context flags
> (see §4/§5). Logins with email/password work immediately.

---

## 4. Google OAuth client (enables "Sign in with Google")

1. [Google Cloud Console](https://console.cloud.google.com/) → create/select a project.
2. **APIs & Services → OAuth consent screen** → External → fill in app name, support email,
   developer email → Save. While in **Testing**, add your Google address under **Test users**.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID → Web application**.
4. **Authorized redirect URIs** — add exactly:
   ```
   https://lexicon-<YOUR_ACCOUNT_ID>.auth.us-east-1.amazoncognito.com/oauth2/idpresponse
   ```
5. Copy the **Client ID** and **Client secret**.
6. Store the secret (raw string) in Secrets Manager:
   ```bash
   aws secretsmanager create-secret --region us-east-1 \
     --name lexicon/google-oauth --secret-string '<GOOGLE_CLIENT_SECRET>'
   ```
7. Redeploy with the client id:
   ```bash
   cd infra/cdk
   cdk deploy \
     -c google_client_id='<GOOGLE_CLIENT_ID>.apps.googleusercontent.com' \
     -c google_secret_name='lexicon/google-oauth'
   ```

---

## 5. GitHub OAuth App (enables "Sign in with GitHub")

1. GitHub → **Settings → Developer settings → OAuth Apps → New OAuth App**.
2. **Authorization callback URL** — exactly:
   ```
   https://lexicon-<YOUR_ACCOUNT_ID>.auth.us-east-1.amazoncognito.com/oauth2/idpresponse
   ```
3. Generate a client secret; copy **Client ID** and **Client secret**.
4. Store the secret:
   ```bash
   aws secretsmanager create-secret --region us-east-1 \
     --name lexicon/github-oauth --secret-string '<GITHUB_CLIENT_SECRET>'
   ```
5. Redeploy with the client id (combine with the Google flags if desired):
   ```bash
   cd infra/cdk
   cdk deploy \
     -c github_client_id='<GITHUB_CLIENT_ID>' \
     -c github_secret_name='lexicon/github-oauth'
   ```

---

## 6. Local environment files

Copy the templates and fill in the stack outputs.

```bash
cp python/.env.example python/.env
cp apps/web/.env.example apps/web/.env
```

For the **desktop app**, export these before launching (or put them in your shell profile):

```bash
export LEXICON_API_BASE='<ApiUrl>'
export LEXICON_COGNITO_DOMAIN='<CognitoHostedUiDomain>'
export LEXICON_COGNITO_CLIENT_ID='<UserPoolClientId>'
export LEXICON_COGNITO_CALLBACK_URL='http://127.0.0.1:4200/callback'
```

> `http://127.0.0.1:4200/callback` is already registered as an allowed callback URL on the app
> client. If you change the port, add the new URL via CDK context:
> `-c extra_callback_urls='["http://127.0.0.1:4321/callback"]'`.

---

## 7. Verify

```bash
# Ingest the sample corpus (needs AWS creds + step 2 done)
cd python && python -m lexicon seed ../data/seed/sample_terms.jsonl
python -m lexicon search "grounding a model in sources" --domain ai
python -m lexicon ask "quiz me on advanced AI terms" --mode quiz

# Web dev server
cd apps/web && npm run dev
```

To confirm auth end to end, sign in from the desktop app and check the account appears under
**Cognito → User pool → Users**.

---

## 8. IAM permissions

There are **two** identities. Don't confuse them.

### A. Your deployer identity (you, via AWS CLI / CDK / local Python)

This is the human/role that runs `cdk deploy`, `bootstrap_s3vectors.py`, `build-corpus`, and
`seed`. It needs broad permissions because it creates the whole stack.

> **Simplest:** attach `AdministratorAccess` for setup, then tighten later. CDK bootstrapping
> creates IAM roles, an S3 bucket, and an ECR repo, which is fiddly to scope beforehand.

**Or attach exactly what this repo needs.** The complete policy is
[`infra/iam/deployer-policy.json`](../infra/iam/deployer-policy.json) (13 statements):

```bash
# Create the policy once, then attach it to your user
aws iam create-policy \
  --policy-name LexiconDeployer \
  --policy-document file://infra/iam/deployer-policy.json

aws iam attach-user-policy \
  --user-name <YOUR_USER_NAME> \
  --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/LexiconDeployer
```

Or as an inline policy (no separate policy resource):

```bash
aws iam put-user-policy \
  --user-name <YOUR_USER_NAME> \
  --policy-name LexiconDeployer \
  --policy-document file://infra/iam/deployer-policy.json
```

> **`file://` gotcha:** the AWS CLI resolves it relative to your **current directory**, not the
> repo. Run the commands from the repo root, or use an absolute path with three slashes:
> `file:///Users/you/Development/ai/lexicon/infra/iam/deployer-policy.json`. Tilde (`~`) is not
> expanded inside a `file://` URL. Symptom of getting this wrong:
> `Unable to load paramfile file://...: No such file or directory`.

**Notes**

- Covers CloudFormation, IAM (stack roles), Lambda, DynamoDB, Cognito, Secrets Manager,
  S3 Vectors, Bedrock invoke, CloudWatch Logs, the CDK staging bucket, ECR (Lambda bundling),
  and SSM lookups.
- It is intentionally broad on a few services (`lambda:*`, `dynamodb:*`, `cognito-idp:*`,
  `s3vectors:*`, `ecr:*`, `secretsmanager:*`) because CDK creates resources whose names aren't
  known at synth time. It is still far narrower than `AdministratorAccess` — no other AWS
  services are touched.
- **Bedrock model access is separate from IAM.** This policy allows `bedrock:InvokeModel`, but
  you'll still get `AccessDeniedException` until the models are enabled in the console (§1).
- To tighten further later: once the stack exists, replace the service wildcards with the
  concrete stack/function ARNs, or move deployment to a dedicated role.

### B. The Lambda execution role (created for you by CDK)

You don't create this — the stack does. It is already least-privilege:

| Service | Permissions | Scope |
|---|---|---|
| CloudWatch Logs | `AWSLambdaBasicExecutionRole` (managed) | the function's log group |
| DynamoDB | read/write (`GetItem`, `PutItem`, `Query`, `DeleteItem`, `BatchWriteItem`, …) | the `lexicon` table only |
| Bedrock | `InvokeModel`, `InvokeModelWithResponseStream` | foundation models + inference profiles |
| S3 Vectors | `QueryVectors`, `GetVectors`, `ListVectors`, `PutVectors`, `DeleteVectors` | the `lexicon-vectors` bucket + indexes |

No long-lived credentials are involved — Lambda assumes this role automatically, and the client
never sees AWS credentials at all.

---

## What you do NOT need

- **No custom domain.** Cognito's Hosted UI domain is free and AWS-managed.
- **No certificates.** Cognito, Lambda Function URLs, and (later) GitHub Pages all provide
  free/managed HTTPS.
- **No Apple Developer account.** Apple Sign-In is deferred; a PWA needs no App Store rule.
