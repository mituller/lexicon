> **⚠️ SUPERSEDED (in part):** This opening section was written for a Python-only RAG spec.
> The **actual build target is a React 19 + TypeScript frontend, a Python runtime
> (`retriever.py`, `tutor_agent.py`, FastAPI on Lambda), a thin Rust Tauri desktop shell, and
> Python for offline ingestion**, with Cognito auth and DynamoDB progress. See the
> **Addendum (§1a)** for locked decisions and **§2** for remaining questions. The Python modules
> below are now the spec for both ingestion **and** runtime retrieval/tutor.

You are an expert cloud architect and full-stack AI engineer. Your task is to build a complete, production-ready Python backend application for a RAG-enabled Vocabulary Learning Platform.

As you build out this application, ask questions or give directions on items that are needed form me. S3 storage, Bedrock information, etc.

### 🎯 Project Overview
The application is a custom RAG (Retrieval-Augmented Generation) vocabulary tutor. Instead of using traditional vector databases, it stores and queries vectors natively using **Amazon S3 Vectors** and uses **Amazon Bedrock** for both text embeddings and LLM generation. It supports multi-domain learning (e.g., General English + niche technical domains like Artificial Intelligence) and multi-tier difficulty mapping (CEFR levels and difficulty tiers).

---

### 🏗️ Tech Stack & Dependencies
- **Language:** Python 3.10+
- **Cloud SDK:** `boto3`
- **Vector Storage & Search:** Amazon S3 Vectors
- **Embedding Model:** Amazon Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`) via Amazon Bedrock Runtime
- **Generation Model:** Amazon Nova Lite (`us.amazon.nova-lite-v1:0`) via Amazon Bedrock Runtime

I want the application to follow the same structure on the frontend as in another app. This is 
---
 ### 🏗️ Core Technology Stack

 - Framework: React 19 (JavaScript library for UI).
 - Language: TypeScript (for type-safe business logic).
 - Runtime Environment: Tauri WebView (the core application window is a web view hosted by the Tauri native shell).
 - Build Tooling: Vite (used for development and bundling).

 ### 🧱 Architectural Layers

 1. Frontend (src/):
     - What it handles: All User Interface (UI) logic, business logic, state derivation, and rendering.
     - Interaction: It does not handle heavy background tasks or direct OS access. It communicates with the Rust backend using invoke() (Tauri's RPC
       mechanism) and Tauri Plugin APIs.
     - AI Safety Pattern: The frontend must never call external AI APIs directly. All AI requests are routed through a secure Tauri command (invoke_bedrock)
       implemented in the Rust backend.

 2. Backend (src-tauri/):
     - What it handles: The thin, high-performance native layer written in Rust. This layer registers plugins and exposes native commands (invoke_bedrock,
       audio capture functions, etc.).
     - Function: It acts as a secure bridge between the JavaScript environment and the OS/heavy processes.

 ### 💾 Data Persistence and State Management

 - Three Stores: Data is managed across three tiers:
     1. SQLite (nexus.db): The primary persistent store for structured data (tasks, notes, wiki pages).
     2. JSON Store (nexus-settings.json): Stores configuration (AppSettings) and cached briefings.
     3. In-memory (Zustand): Used for derived state and fast access during runtime.
 - State Management: The frontend relies heavily on Zustand stores (src/stores/).
     - Pattern: Stores are initialized by a load() function that fetches data from SQLite upon startup. Mutations then write back to SQLite before updating
       the in-memory Zustand state immediately.
     - Inter-Store Communication: Stores communicate with each other using .getState() to avoid creating re-render loops.

 ### 📡 Key Patterns & Communication

 - Native Calls: Communication from React to Rust is achieved via invoke("commandName", { data }).
 - External I/O: All external HTTP requests (e.g., GitLab API, iCal feeds) must use fetch provided by the specific Tauri plugin ("@tauri-apps/plugin-http").
   Using the standard window.fetch is blocked due to WebView CORS restrictions.
 - Specialized I/O (Audio): Audio recording and transcription are handled by dedicated threads in the Rust backend (src-tauri/src/audio.rs). This is done
   because the WebView environment does not reliably expose mic capture APIs needed for Transcribe.

 In short, it's a classic "React + State Management + Native Bridge" setup, heavily emphasizing security and separation of concerns by keeping all sensitive
 operations (API keys, file I/O, heavy computation) in the Rust layer.

### 📋 Data Schema Specification
Every vocabulary record ingested into S3 Vectors must adhere to this structure:

1. **Filterable Metadata (Indexed for rapid boolean/range filtering in S3 Vectors):**
   - `domain` (String): e.g., `"ai"`, `"general"`
   - `subfield` (String): e.g., `"llm_architecture"`, `"linguistics"`
   - `difficulty_tier` (String): e.g., `"beginner"`, `"intermediate"`, `"advanced"`
   - `cefr_level` (String): e.g., `"A1"`, `"B1"`, `"C1"`

2. **Non-Filterable Metadata (Stored as rich payload data returned with search results):**
   - `term_id` (String): Unique identifier (e.g., `"ai_rag_001"`)
   - `term` (String): The vocabulary word or phrase
   - `definition` (String): Clear, concise definition
   - `example` (String): Real-world usage sentence
   - `synonyms` (List[String] or comma-separated string)

---

### 📦 Required Code Modules to Implement

Please provide clean, modular, well-documented Python code implementing the following three core components:

1. **`ingestion.py` (Data Seeding & Ingestion Pipeline):**
   - A function that takes raw vocabulary JSON records.
   - Combines the term, definition, and example into a semantic text string.
   - Calls Amazon Bedrock (`amazon.titan-embed-text-v2:0`) to generate the vector embedding.
   - Upserts the record into Amazon S3 Vectors, properly segregating `filterableMetadata` and payload `metadata`.

2. **`retriever.py` (Filtered Semantic Search):**
   - A function that takes a user query string, target domain, and target difficulty tier.
   - Generates the query embedding via Bedrock.
   - Queries Amazon S3 Vectors using native metadata filtering (e.g., filtering for `domain == "ai"` and `difficulty_tier == "advanced"`).
   - Returns the top-$k$ most relevant vocabulary documents.

3. **`tutor_agent.py` (RAG Orchestration & Generation):**
   - A prompt template engineered for an expert language and technical tutor.
   - Takes the retrieved context and user request.
   - Calls Amazon Nova Lite (`us.amazon.nova-lite-v1:0`) via Bedrock to formulate a helpful, structured educational response (including definitions, usage nuances, or practice quiz generation).

4. **`main.py` (Execution Entry Point):**
   - A script demonstrating an end-to-end workflow: seeding sample AI/General data, running a filtered search query, and generating a tutor response.

---

### ⚙️ Output Requirements
- Provide complete, copy-pasteable Python code files with proper error handling and type hints.
- Include a brief setup guide detailing required AWS IAM permissions (Bedrock invoke permissions and S3 Vectors permissions) and environment variable configurations.

---

# 🧭 Addendum: Multi-Platform + Auth + User Data (Draft Plan for Review)

> **Status:** DRAFT — core direction is **LOCKED** (see §1a). Remaining items needing your
> input are listed in §2.
> This addendum supersedes the original "Python backend only" framing where it conflicts.

## 1. Target Architecture (locked)

**One React 19 + TypeScript frontend, three delivery targets, a Python RAG/cloud API, and a
thin Rust desktop shell.**

**Key idea:** all RAG + cloud logic lives in **one Python package** (`ingestion`, `retriever`,
`tutor_agent`) that backs a FastAPI service on Lambda. The desktop app (Tauri) is a thin Rust
shell for OS access, local SQLite, and offline queueing; the web/PWA uses the identical API.

### 1a. ✅ Locked Decisions (from your answers)

| # | Decision | Choice |
|---|---|---|
| L1 | Backend split | **Python runtime + thin Rust shell.** Python owns `ingestion.py`, `retriever.py`, `tutor_agent.py`, and the FastAPI cloud API. Rust owns the Tauri desktop shell, keychain, local SQLite, and offline outbox. (Resolves Q-a.) |
| L2 | Mobile | **PWA / mobile web only.** No App Store or Play Store. Same web build, installed via "Add to Home Screen". |
| L3 | Auth provider | **Amazon Cognito User Pool** + Hosted UI. |
| L4 | Identity providers | **Google, Apple, GitHub, and email** (email/password + email OTP). Apple is required for iOS PWA parity; GitHub for dev-centric users. |
| L5 | User progress store | **DynamoDB** single-table, keyed by Cognito `sub`. |
| L6 | User-created lists | **Yes — users can create their own vocabulary lists/sets.** Private per-user by default. Stored in the same S3 Vectors index with filterable `owner_id` + `scope` metadata (⚠️ S3 Vectors has **no namespaces** — isolation is by metadata filter, not namespace), mirrored in DynamoDB. |
| L7 | Frontend | React 19 + TypeScript + Vite + Zustand, AI-safety pattern (no direct AI calls from frontend). |
| L8 | Vector store / models | S3 Vectors; Bedrock Titan Text Embeddings V2 + Nova Lite. |
| L9 | Desktop shell | Tauri v2, reusing the same web build. |
| L10 | Naming | **`lexicon`** everywhere; inherited `nexus` naming is dropped. |
| L11 | Web/phone hosting | **GitHub Pages** (static PWA) to start; optionally move to S3 + CloudFront after completion. |
| L12 | Repo | Monorepo at `mituller/lexicon` (assumed public — see Q-t). |
| L13 | Web/phone API host | **AWS Lambda** + Function URL (locked), running **Python FastAPI** (Mangum). |
| L14 | Build order | **Desktop-first.** Web/PWA is stubbed behind a transport interface and added later. |
| L15 | Desktop execution | Rust shell (keychain/SQLite/outbox) → **Python API** over HTTPS + JWT. No Cognito Identity Pool needed. (Resolves Q-b.) |
| L16 | AWS region | **`us-east-1`** for all AWS resources (Cognito, DynamoDB, S3 Vectors, Bedrock, Lambda). (Resolves Q-j.) |
| L17 | Progress model | **FSRS per-term state** + per-user aggregate derived from it. (Resolves Q-i/Q-p.) |
| L18 | Corpus source | **CEFR-J Vocabulary Profile + Octanove C1/C2** as the seed word list; **Bedrock generates definitions/examples/synonyms**. (Resolves Q-n/Q-o.) |
| L19 | Auth at launch | **Google + GitHub + email**; Apple deferred until a custom domain exists. No domain or purchased certificate required. (Resolves Q-g/Q-h.) |
| L20 | Domain | **None for v1.** Desktop-only build needs no domain. Web/PWA (and any custom domain) is deferred with it. |

```
┌──────────────┐   ┌───────────────┐   ┌───────────────┐
│ Desktop app  │   │    Web app    │   │  Phone (PWA)  │
│   (Tauri)    │   │ (GitHub Pages)│   │   (browser)   │
└──────┬───────┘   └───────┬───────┘   └───────┬───────┘
       │ invoke()          │  HTTPS + JWT      │  HTTPS + JWT
       ▼                   └─────────┬─────────┘
┌──────────────────────┐             │
│ Rust shell (thin)    │             │
│ keychain · SQLite ·  │             │
│ offline outbox       │             │
└──────────┬───────────┘             │
           └──── HTTPS + JWT ────────┘
                                     ▼
                     ┌──────────────────────────────────┐
                     │ Python API — FastAPI on Lambda    │
                     │ retriever · tutor_agent · progress│
                     └──────────┬───────────────────────┘
                                ▼
       ┌────────────────────────────────────────────┐
       │ AWS: Cognito (Google/Apple/GitHub/email),  │
       │ DynamoDB, S3 Vectors, Bedrock  (us-east-1) │
       └────────────────────────────────────────────┘

  Python (offline): ingestion.py → Titan embed → S3 Vectors
```

### Component choices

| Concern | Choice | Why |
|---|---|---|
| Frontend | React 19 + TS + Vite + Zustand | same patterns as your existing app |
| Desktop | Tauri v2 + **thin Rust shell** (keychain, SQLite, outbox) | native window, offline, minimal Rust you have to read |
| Web + phone | Same Vite build → **GitHub Pages** (installable PWA) + Cognito | free static hosting, no app-store work; S3+CloudFront optional later |
| Runtime / API | **Python FastAPI on Lambda** (Function URL) | `boto3` is first-class for Bedrock + S3 Vectors; easiest to read |
| Auth | Cognito User Pool, IdPs: Google, Apple, GitHub, email | AWS-native, OIDC, cheap, works for Tauri + web |
| User data store | DynamoDB single-table (`pk=USER#<sub>`) | serverless, per-user isolation, scales to zero |
| Global corpus | S3 Vectors index, `scope="global"`, read-only | no re-embedding cost, shared by all users |
| User lists | Same index, `scope="user"` + `owner_id=<sub>` (filterable) + DynamoDB metadata | isolates private content via query filter |
| Embeddings / LLM | Bedrock Titan V2 + Nova Lite | already decided |
| Offline | Desktop: local SQLite mirror + outbox queue. Web/phone: online-only, IndexedDB read cache | graceful degradation per platform |

### Language split: Python runtime, thin Rust shell

**Decision:** the **RAG runtime lives in Python** (`retriever.py`, `tutor_agent.py`, and the
cloud API), not Rust. Rust remains on the desktop as a **thin Tauri shell** for OS access,
secure token storage, local SQLite, and offline queueing. This is deliberate: Python's `boto3`
is the reference SDK for Bedrock and S3 Vectors, and it's far easier for you to read and modify.

**Will it work well?** Yes:

- **Bedrock generation (Nova Lite) dominates end-to-end latency** (seconds). Python's Lambda
  cold start (~200–500 ms) and interpreter overhead are negligible next to it.
- Python is a **managed Lambda runtime** — no custom runtime or `cargo-lambda` ceremony, so the
  deploy path is simpler than Rust's would be.
- `boto3` has first-class support for **S3 Vectors** and **Bedrock**, so this code is shortest in
  Python.

**What changes vs. the earlier Rust-runtime idea:**

| Concern | Before (Rust runtime) | Now (Python runtime) |
|---|---|---|
| Retriever / tutor | Rust in `lexicon-core` | **Python** — same `retriever.py` / `tutor_agent.py` from the original spec |
| Cloud API host | Rust Axum on Lambda | **Python FastAPI** on Lambda (Mangum / Lambda Web Adapter) |
| Desktop AI calls | Direct from Tauri (no hop) | Desktop → Python API (one hop) — fine, Bedrock is already remote |
| Rust's role | All business logic | Thin shell: window, keychain, SQLite, offline outbox, `invoke()` |
| Cognito Identity Pool | Needed for desktop direct AWS access | **Not needed** — the Python API holds the AWS role |

**Desktop local features are unaffected:** SQLite, offline review, keychain, and the app shell
stay in Rust. AI/tutor features need connectivity either way because Bedrock is remote.

**Future option (not now):** bundle a **Python sidecar** (PyInstaller) with the Tauri app to run
retriever/tutor locally and drop the hop. Adds packaging complexity — skip for v1.

### Cloud API: Python FastAPI on Lambda

- **Framework:** FastAPI; deploy with **Mangum** (ASGI→Lambda adapter) or **Lambda Web Adapter**
  + uvicorn. Put a **Function URL** in front (no API Gateway needed initially).
- **Auth:** verify the Cognito JWT on every request (JWKS from the User Pool); derive `userId`
  from `sub`. Never trust a client-supplied user id.
- **AWS access:** via the function's IAM role — no credentials in the client.
- **Streaming:** return Bedrock tokens with `StreamingResponse` (Function URL response streaming)
  for fast time-to-first-token.
- **Shared code:** `ingestion.py`, `retriever.py`, `tutor_agent.py` live in **one Python package**
  used by both the offline CLI and the Lambda handler.

### Lambda performance notes (Python)

- **Cold start is the only real cost** (~200–500 ms). Keep it modest: lazy-import heavy modules,
  build `boto3` clients once at module scope, and give the function **≥1024 MB** (more memory →
  more CPU → faster import + Bedrock glue).
- **Reuse clients across invocations** — create Bedrock/DynamoDB/S3 Vectors clients outside the
  handler so warm invocations skip boto3 setup.
- **Use a Lambda Function URL directly** (skip API Gateway) for one less hop.
- **Keep embeddings off the hot path.** Global vectors are precomputed; a request embeds only the
  user's query (one Titan call) plus one Nova Lite generation. Cache query embeddings (in-process
  LRU + DynamoDB) to skip repeats.
- **Bedrock dominates latency**, so Lambda overhead is minor — stream tokens for a fast first byte.
- **Colocate everything in `us-east-1`**; set the timeout to cover generation (30–60 s; max
  15 min); use DynamoDB on-demand.
- **If the cold-start tail ever matters**, add provisioned concurrency on the API alias — not
  needed at launch. Track p95 with Powertools/CloudWatch from day one.

### Execution paths: desktop vs web (desktop-first)

| Path | How it runs | Notes |
|---|---|---|
| **Desktop (Tauri)** | Rust shell (keychain, SQLite, offline outbox) → Python API over HTTPS + JWT | one hop; AI needs the network anyway |
| **Web/phone (later)** | Browser → same Python API (Function URL) over HTTPS + JWT | identical API; web is stubbed now |

Both clients hit the **same FastAPI endpoints**, so shipping web later is a hosting + Cognito
config change, not a logic port. The frontend reaches it through one `Transport` abstraction
(`invoke()` on desktop, `fetch` on web).

### Auth flow details (Cognito)
- **Web/phone:** OAuth 2.0 Authorization Code + PKCE against the Cognito Hosted UI (or an
  in-app login screen using Amplify/Cognito SDK) → ID/access/refresh tokens.
- **Tauri desktop:** open Cognito Hosted UI in the **system browser** (not the WebView),
  capture the redirect on a loopback port (`http://127.0.0.1:<port>/callback`) or a custom
  URI scheme (`lexicon://auth/callback`), then store tokens in the OS keychain, not SQLite.
- **Token→user map:** use the Cognito `sub` claim as `userId`. Never trust a client-supplied
  user id; the API derives it from the verified JWT.
- **IdP provisioning (launch: Google + GitHub + email):**
  - **Google:** Google Cloud OAuth client (Client ID/Secret); redirect URI
    `https://<cognito-domain>/oauth2/idpresponse`.
  - **GitHub:** GitHub OAuth App (Client ID/Secret); redirect URI
    `https://<cognito-domain>/oauth2/idpresponse`.
  - **Apple (deferred):** not at launch. Later it needs an Apple Developer Service ID + key,
    a **verified domain you own**, and the domain-association file; see Q-g.
  - **Email:** Cognito native email/password + email OTP (SES for delivery); no external
    client needed.

### GitHub Pages hosting: what it does and does NOT cover

GitHub Pages is **static file hosting only** — perfect for the React PWA, but it cannot run
any server code. Practical consequences baked into the plan:

| Concern | Action |
|---|---|
| Frontend/PWA | ✅ Works. Vite build → `gh-pages` via GitHub Actions. |
| Python API (auth-dependent data, RAG) | ❌ Not hosted here — runs on Lambda (see below). |
| Cognito login | ✅ Works: Hosted UI + PKCE from a static origin; add the Pages URL to app-client callback/logout allow-lists. |
| CORS | The Lambda Function URL must allow the Pages origin (`https://<user>.github.io`). |
| SPA deep links | GitHub Pages 404s on client routes → add `public/404.html` fallback. |
| Sub-path | Repo pages serve under `/<repo>/` → set Vite `base: '/lexicon/'` (or use a custom domain to serve at root). |
| Service worker scope | PWA manifest/service worker must use the same base path so install + offline cache work. |
| Apple Sign-In | Needs a verified domain; GH Pages custom domain (with HTTPS) works, and requires uploading `apple-developer-domain-association` to `/.well-known/`. |
| Public repo | Classic GitHub Pages needs a **public** repo (or GitHub Pro for private Pages). |
| Secrets | No secrets in the static bundle — only Cognito public client IDs. All AWS keys stay in the API/Actions secrets. |

**Nothing else in the AWS stack changes:** Cognito, DynamoDB, S3 Vectors, and Bedrock all still
live on AWS regardless of where the static site is served. Moving to S3 + CloudFront later is a
hosting swap only (same build, different upload target).

### Security rules
- The frontend never holds AWS credentials and never calls Bedrock/S3 directly.
- **Desktop:** the Rust shell stores Cognito tokens in the OS keychain and attaches the JWT to
  calls to the Python API; it never signs AWS requests itself.
- **Web:** the browser talks HTTPS + Bearer JWT to the Python API only.
- **Server:** the Lambda function's IAM role holds the only AWS permissions; per-user
  authorization is enforced from the verified JWT `sub`.
- No long-lived AWS keys anywhere in a client; no Cognito Identity Pool needed.

---

## 2. ❓ Remaining Questions I Need You to Answer

### A. Language boundary (✅ resolved)
- **Q-a. ✅ Resolved:** **Python owns the runtime** — `ingestion.py`, `retriever.py`,
  `tutor_agent.py`, and the FastAPI API. Rust is a thin Tauri shell (keychain, SQLite, offline
  outbox). See "Language split" above for why this is performant.
- **Q-b. ✅ Resolved:** desktop and web both call the **Python API**; no Cognito Identity Pool
  and no direct AWS access from the client.
- **Q-c. ✅ Resolved:** naming is **`lexicon`** throughout; inherited `nexus` naming is dropped.

### B. Platform details
- **Q-d.** Desktop targets: macOS only, or also Windows/Linux? (Recommend: macOS first.)
- **Q-e.** Offline sync: is **last-write-wins** acceptable, or do you want a merge/conflict log?
  (Recommend: last-write-wins with per-term `updatedAt` + tombstones.)

### C. Auth details
- **Q-f.** Guest/anonymous mode before login? (Recommend: no at launch.)
- **Q-g/Q-h. ✅ Resolved — you do NOT need a domain or a purchased certificate for launch.**
  - **Google / GitHub / email:** no custom domain needed. Cognito gives a free Hosted UI domain
    (`https://<prefix>.auth.us-east-1.amazoncognito.com`); you register
    `https://<cognito-domain>/oauth2/idpresponse` as the redirect URI in Google/GitHub. Done.
  - **Certificates: you never buy one.** Cognito's domain, Lambda Function URLs, and GitHub
    Pages all use managed/free HTTPS (GitHub Pages auto-issues a free Let's Encrypt cert). ACM
    certs are also free if you later add a CloudFront/API Gateway domain.
  - **Apple is the only exception** — Sign in with Apple requires a **domain you own** plus a
    verification file at `/.well-known/apple-developer-domain-association.txt`, and an Apple
    Developer Program membership ($99/yr). `*.github.io` can't be verified.
  - **Recommendation:** **ship Google + GitHub + email now; drop or defer Apple.** Because this
    is a PWA (no App Store), Apple Sign-In is **not required by any store rule** — it was only
    for iOS parity. Add a cheap domain (~$10–15/yr) + Apple later if you want it.
  - **What you create now:** a Google Cloud OAuth client + a GitHub OAuth App (both free,
    ~5 min each). Step-by-step setup written when we wire up Phase 1.

### D. Data & AWS
- **Q-i. ✅ Resolved — store per-term FSRS state; the per-user aggregate is derived from it.**
  These are not alternatives: the aggregate is *computed from* the per-term records, and FSRS
  (Q-p) cannot work without per-term state. So the per-term record is **required**; the
  aggregate is a rollup the API recomputes.

  **Per-term record** (one per user + term) — FSRS fields:

  | Field | Meaning |
  |---|---|
  | `state` | `new` · `learning` · `review` · `relearning` |
  | `due` | next review datetime |
  | `stability` (S) | memory stability in days (FSRS) |
  | `difficulty` (D) | 1–10 item difficulty (FSRS) |
  | `elapsed_days` / `scheduled_days` | days since last review / interval last set |
  | `reps` / `lapses` | total reviews / times forgotten |
  | `last_review` | timestamp of the last review |
  | `lastRating` | Again(1) · Hard(2) · Good(3) · Easy(4) |
  | `note`, `starred` | the user's own note + favourite flag |

  **Per-user aggregate** (recomputed, never hand-edited): `currentStreak`, `longestStreak`,
  `lastStudyDate`, `dailyGoal`, `activeListIds`, `targetCefr`, `preferredDomains`, plus derived
  counts (`totalLearned`, `dueToday`). The UI `mastery` badge (0–5) is derived from `state` +
  `stability`. `totalXp` is optional — say if you want it.
- **Q-j. ✅ Resolved:** **`us-east-1`** for every AWS resource (Cognito, DynamoDB, S3 Vectors,
  Bedrock, Lambda).
- **Q-k.** Personal project or multi-tenant SaaS? Any **cost ceiling** to design against?
- **Q-l.** Data **export/delete** (account deletion) required? (Recommend: yes, one endpoint each.)
- **Q-m.** Should user-created lists be **shareable/publishable** later, or private-only forever?
  (Affects whether we build a publish flow + moderation.)

### E. Content & learning logic
- **Q-n. ✅ Resolved — CEFR-J as the seed word list, Bedrock for definitions.** Neither dataset
  contains definitions or examples, so the pipeline is: **word list + CEFR level from CEFR-J →
  Bedrock generates definition/example/synonyms → embed → S3 Vectors.**

  | Source | Coverage | License | Our use |
  |---|---|---|---|
  | **CEFR-J Vocabulary Profile** (`openlanguageprofiles/olp-en-cefrj`, v1.5) | Pre-A1 → **B2**, human-curated, includes POS + topic categories (`CoreInventory`) | **Free for research *and* commercial** with citation (© Tono Lab, TUFS) | **Primary seed list** — CEFR levels + `subfield` topics |
  | **Octanove Vocabulary Profile C1/C2** (same repo) | **C1 / C2** | **CC BY-SA 4.0** (attribution + share-alike) | **Extends coverage to C2** (schema needs C1) |
  | Maximax67 `Words-CEFR-Dataset` | every valid English word incl. inflections | MIT (derived from CEFR-J + Google N-grams) | **Supplementary** — POS/lemma/frequency lookup for matching user input, *not* authoritative levels |

  **Why CEFR-J over Maximax67 as primary:** it's the **authoritative human-curated** CEFR source
  (right for teaching), it ships **topic metadata** that maps to `subfield`, and with Octanove it
  covers **C1/C2** — whereas Maximax67 interpolates levels statistically and includes proper
  nouns/names. That interpolation is ideal for *lookups*, not for the taught curriculum.
  **Licensing note:** the derived corpus file should carry **CC BY-SA 4.0** + the TUFS citation
  if distributed — fine for a personal app, matters if you commercialize. **Sizing:** take a
  curated subset per level/domain (~300–500 general + ~150–250 AI terms for v1).
- **Q-o. ✅ Resolved:** `cefr_level` comes from CEFR-J/Octanove; `difficulty_tier` is a simple
  mapping (A1–A2 → beginner, B1–B2 → intermediate, C1–C2 → advanced); `subfield` comes from
  CEFR-J `CoreInventory` topics for general words and is Bedrock-assigned for AI/technical terms.
- **Q-p. ✅ Resolved — FSRS.** Store the FSRS state fields in Q-i and use the reference `fsrs`
  Python library behind a `SchedulingService` interface so the algorithm stays swappable.
  Ratings map to Again/Hard/Good/Easy (1–4).
- **Q-q.** Tutor scope at launch: **quiz + definition + usage examples** only, or also
  pronunciation (audio), writing feedback, conversation practice?

### F. Delivery
- **Q-r. ✅ Resolved (proposed):** **desktop-first vertical slice** (login → local progress →
  one filtered RAG query through the Python API). Build the web/PWA later on the same API; the
  web transport is **stubbed** now so nothing blocks.

### G. Hosting (after the GitHub Pages decision)
- **Q-s. ✅ Resolved:** the API runs as **Python FastAPI on AWS Lambda** (Function URL), managed
  runtime + Mangum. GitHub Pages is static-only, so the API lives here; no container needed.
- **Q-t.** Is the GitHub repo **public**? Classic GitHub Pages requires a public repo
  (private Pages needs GitHub Pro). Recommend public app repo, secrets in Actions.
- **Q-u.** Do you want a **custom domain** now (e.g. `lexicon.<yourdomain>`)? Benefits:
  serves at `/` instead of `/<repo>/`, looks cleaner on phones, and satisfies Apple's
  domain-verification requirement. Otherwise default to `https://<user>.github.io/lexicon/`.

**Answer these in-line below, or just reply in chat and I'll fold them back in.**

---

## 3. Phased Plan (core locked; a few details pending §2)

### Phase 0 — Remaining decisions & skeleton
- Answer the remaining §2 items (at minimum **Q-a, Q-j, Q-i, Q-p, Q-n**; Q-t/Q-u matter only
  when the web ships). The architecture core is locked in §1a.
- Define the shared **`Transport` trait** (`invoke()` for desktop, `fetch` for web) and stub the
  web implementation, so enabling the web later swaps a transport and nothing in the UI.
- Create monorepo layout:
  ```
  lexicon/
    apps/web/            # React 19 + TS + Vite (shared UI)
    apps/desktop/        # Tauri shell (reuses apps/web build)
    crates/lexicon-shell/ # thin Rust Tauri shell: keychain, SQLite, outbox
    python/lexicon/      # ONE Python package, shared by CLI + API
      ingestion.py       #   corpus prep, Titan embed, S3 Vectors loader, QA
      retriever.py       #   filtered semantic search
      tutor_agent.py     #   Nova Lite RAG orchestration
      api.py             #   FastAPI app (Mangum) → AWS Lambda + Function URL
    .github/workflows/   # ci.yml + deploy-web.yml (GitHub Pages)
    public/404.html      # SPA deep-link fallback for GitHub Pages
    infra/               # CDK or Terraform: Cognito, DDB, S3 Vectors, API, (S3+CloudFront opt.)
    data/                # seed vocabulary JSON
  ```
- Set up CI: `ruff`/`mypy` + `pytest` (Python), `tsc` (frontend), `cargo fmt`/`clippy` + tests
  (thin Rust shell).

### Phase 1 — Identity (auth end-to-end) ✅ code complete — AWS provisioning pending
- ✅ CDK: Cognito User Pool, Hosted UI domain, app client (public/PKCE), Google/GitHub IdPs as
  **optional context flags** so the stack deploys before the OAuth apps exist.
- ✅ Desktop login: system-browser Hosted UI + **loopback PKCE** in Rust (`auth.rs`), tokens
  cached in memory (keychain persistence TODO); `auth_login` / `auth_logout` / `auth_status`.
- ✅ Web login: Hosted UI + PKCE in TypeScript (`lib/auth.ts`) — prepared, not deployed.
- ✅ API: JWT verified against Cognito JWKS; `userId` from `sub`; `GET /v1/me` to confirm.
- ✅ Python PKCE/authorize/logout helpers (`lexicon/cognito.py`) with tests.
- ⏳ **Needs you:** enable Bedrock access, run the S3 Vectors bootstrap, `cdk deploy`, then
  create the Google/GitHub OAuth apps and re-deploy with their ids. See `docs/setup.md` §1–§5.
- **Exit criteria:** the same account logs into the desktop app and `/v1/me` returns one `userId`
  across providers.

### Phase 2 — Progress persistence ✅ code complete
- ✅ DynamoDB single-table (`USER#<sub>` + `PROGRESS#`/`LIST#`/`AGGREGATE`) + typed repo.
- ✅ FSRS review applied via `PUT /v1/progress/{term_id}`; aggregate + streak recomputed
  (`lexicon/progress.py`) and covered by unit tests.
- ✅ React state via Zustand with optimistic review + stats refresh.
- ✅ Desktop offline: SQLite read-cache + review **outbox** in the Rust shell
  (`apps/desktop/src-tauri/src/store.rs`). FSRS stays in Python, so the shell queues review
  intents and drains them via `sync_outbox`, writing the server's result back to the cache.
  UI shows a "waiting to sync" count.
- **Exit criteria:** mark a term learned in the desktop app; it lands in DynamoDB and reloads
  on a fresh install. (Cross-device check waits until web exists.)

### Phase 3 — Vocabulary ingestion + S3 Vectors ✅ code complete
- ✅ Schema fixed (`data/schema.md`); filterable vs non-filterable split matches the index.
- ✅ **Python** ingestion (`lexicon/ingestion.py`): embed via Titan V2, batch-upsert to S3
  Vectors with `scope="global"`.
- ✅ **Corpus generation** (`lexicon/corpus.py` + `python -m lexicon build-corpus`): parses the
  CEFR-J and Octanove CSVs, dedupes/caps per level, generates definitions/examples/synonyms via
  Bedrock (strict-JSON, tolerant parser), and emits schema-conformant JSONL. `--no-generate`
  supports an offline dry run. 10 unit tests, no AWS required.
- ✅ **User list ingestion path:** terms embedded with `scope="user"` + `owner_id=<sub>`;
  searches filter to `$or: [{scope: global}, {owner_id: <sub>}]` (no namespaces in S3 Vectors).
- ⏳ **Needs you:** run the build against real Bedrock once model access is enabled, review the
  output, then `python -m lexicon seed`.
- **Exit criteria:** global corpus queryable with correct filters; a user-created list is
  searchable by its owner and invisible to others.

### Phase 4 — Retrieval + tutor agent (Python runtime, desktop first)
- Implement `retriever.py` / `tutor_agent.py` in the shared Python package and expose them as
  FastAPI endpoints; the Rust shell calls them and forwards results to React via `invoke()`.
- Filtered semantic search (domain + tier + CEFR, scoped to allowed namespaces) with a warm
  `boto3` client and a query-embedding cache.
- Tutor prompt template (definitions, nuance, examples, quizzes).
- Stream Bedrock tokens to the UI via FastAPI `StreamingResponse`.
- **Exit criteria:** "quiz me on advanced LLM terms" returns grounded, cited vocabulary in the
  desktop app; instrument and record p95 latency.

### Phase 5 — Web + phone delivery (STUBBED now; built later)

> Build the desktop app fully first (Phases 0–4 + 6). Leave the web transport stubbed behind the
> shared `Transport` trait so this phase can be turned on without refactoring.

- Configure Vite `base` for the Pages path (or root with a custom domain) and add the
  `public/404.html` SPA fallback for deep links.
- GitHub Actions workflow: build on push → publish `dist/` via `actions/deploy-pages`.
- `vite-plugin-pwa`: web manifest + service worker scoped to the base path; verify
  "Add to Home Screen" install on iOS and Android.
- Add the Pages origin to Cognito callback/logout URLs and to the Python API CORS allow-list.
- If Apple Sign-In: upload `apple-developer-domain-association` to `/.well-known/`.
- Responsive / mobile-first review.
- **(Later/optional)** swap hosting to S3 + CloudFront + custom domain — same build, no code
  change; capture the ACM cert + Route 53 steps then.
- **Exit criteria:** usable on a phone browser over HTTPS, same account/login as desktop, deep
  links and PWA install work.

### Phase 6 — Hardening ✅ partial
- ✅ **IAM least privilege:** CDK now scopes Bedrock to foundation-model + inference-profile ARNs
  and S3 Vectors to this bucket/index (no more `"*"`). DynamoDB via `grant_read_write_data`.
  Secrets Manager is used for the Google/GitHub OAuth client secrets.
- ✅ **Input validation:** `/v1/search` query bounded 1–1000 chars.
- ✅ **Observability:** JSON request logging (`lexicon/logging_setup.py`: method, path, status,
  duration) shipped to CloudWatch; Lambda log group retention capped at 14 days.
- ✅ **Privacy:** `GET /v1/export` (full portable JSON export) and `DELETE /v1/account`
  (removes all DynamoDB items + best-effort cleanup of owned vectors).
- ✅ **Keychain:** desktop refresh token stored in the OS keychain (with a `0600` file
  fallback); silent refresh on startup restores the session.
- ✅ **Lambda tuning** in place: 1024 MB, client reuse, 60 s timeout.
- ⏳ Still open: rate limiting (API Gateway/`reserved_concurrency`), budget alarms, load test,
  accessibility pass, error-state UX polish.

### Phase 7 — Desktop packaging (optional / later)
- Tauri bundling, code signing/notarization, auto-update channel.

---

## 4. Decisions Already Locked (from your message + existing file)
- React 19 + TypeScript + Vite + Zustand frontend architecture and AI-safety pattern
  (frontend never calls AI APIs directly).
- Rust as a **thin** Tauri shell (`invoke()`, keychain, local SQLite, offline outbox) — no business logic.
- Python as the runtime: `ingestion.py`, `retriever.py`, `tutor_agent.py`, FastAPI on Lambda.
- S3 Vectors for vector storage; Bedrock Titan Embeddings V2 + Nova Lite for
  embedding/generation.
- Cognito auth with **Google, GitHub, and email** at launch (Apple deferred until a custom
  domain exists). No domain or purchased certificate required.
- DynamoDB for user progress; per-user custom vocabulary lists.
- **FSRS** spaced repetition over per-term state; per-user aggregate derived from it.
- Corpus seeded from **CEFR-J Vocabulary Profile (+ Octanove C1/C2)**; Bedrock generates the
  definitions/examples/synonyms.
- PWA delivery for phone; no app-store builds. **Web is stubbed and built later; desktop ships first.**
- AWS Lambda (Function URL) running Python FastAPI; desktop and web both call it over HTTPS +
  JWT. Region: `us-east-1`.
- Filterable metadata: `domain`, `subfield`, `difficulty_tier`, `cefr_level`.
- Payload metadata: `term_id`, `term`, `definition`, `example`, `synonyms`.
- Source of truth for the runtime split: L1 in §1a; remaining details in §2.

---

## 5. Next Actions To Unblock Build

> **Phase 0 ✅ complete** — monorepo skeleton.
> **Phase 1 ✅ code complete** — Cognito CDK + desktop loopback PKCE + web PKCE + JWT middleware.
> **Phase 2 ✅ code complete** — DynamoDB table, FSRS reviews, aggregate/streak + offline store.
> **Phase 3 ✅ code complete** — corpus generation + ingestion.
> **Phase 4 ✅ implemented** — retriever + tutor agent (exercised via CLI).
> **Phase 6 ✅ partial** — IAM scoping, validation, JSON logging, export/delete, keychain.
>
> Verified locally: `ruff` + `mypy` + **43 tests** pass; web `tsc` + `vite build` pass; desktop
> `cargo fmt` + `clippy -D warnings` + `check` pass.
>
> **⏳ What needs you in AWS:** see `docs/setup.md` — (1) enable Bedrock model access, (2) run
> the S3 Vectors bootstrap, (3) `cdk deploy`, (4) create Google/GitHub OAuth apps + re-deploy.
>
> **Note:** the installed `fsrs` is v6 — a new card is `State.Learning` (no `State.New`), and
> `Card` no longer carries `reps`/`lapses`. `lexicon/srs.py` adapts to this and maintains those
> counters itself.

All structural decisions are now settled: Python runtime, `us-east-1`, Lambda, desktop-first
with web stubbed, FSRS progress, CEFR-J corpus source, and Google/GitHub/email auth with no
domain or certificate to buy.

**Only soft questions remain (all have safe defaults):**
1. **Q-k** — personal project or multi-tenant SaaS? Any cost ceiling? (Default: personal.)
2. **Q-l** — account deletion + data export? (Default: build both.)
3. **Q-m** — user lists private-only forever, or shareable later? (Default: private-only v1.)
4. **Apple Sign-In** — add later with a custom domain, or skip entirely? (Default: skip.)

**Phase 0 (repo skeleton + infra) and Phase 1 (auth) can start now.** External artifacts
needed (only as each phase is reached):
- **AWS account** — ✅ confirmed available.
- **Bedrock model access in `us-east-1`** — enable `amazon.titan-embed-text-v2:0` and
  `us.amazon.nova-lite-v1:0`, and confirm **S3 Vectors** access. (One-time console step;
  gates Phase 3–4.)
- A free **Google OAuth client** + **GitHub OAuth App** (Phase 1).
- No domain, no certificate, no Apple Developer account.

