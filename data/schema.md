# Vocabulary record schema

The ingestion pipeline consumes **JSON Lines** (`.jsonl`) — one record per line. See
`python/lexicon/ingestion.py::raw_to_term` for the authoritative mapping.

## Fields

| Field | Required | Notes |
|---|---|---|
| `term_id` | ✅ | Unique, stable key. Convention: `<domain>_<topic>_<n>` e.g. `ai_rag_001`. |
| `term` | ✅ | The headword or phrase. |
| `definition` | ✅ | Clear, concise definition. |
| `example` | – | Real-world usage sentence. Strongly recommended. |
| `synonyms` | – | List of strings, or a comma-separated string. |
| `domain` | ✅ | e.g. `general`, `ai`. **Filterable.** |
| `subfield` | – | e.g. `linguistics`, `llm_architecture`. **Filterable.** |
| `cefr_level` | ✅ | `Pre-A1`, `A1`–`C2` (from CEFR-J / Octanove). **Filterable.** |
| `difficulty_tier` | – | `beginner`/`intermediate`/`advanced`. Derived from `cefr_level` if omitted. |
| `pos` | – | Part of speech (from CEFR-J). Stored non-filterable. |
| `source` | – | Provenance, e.g. `cefr-j`, `bedrock-generated`, `user`. |
| `scope` | – | `global` (default) or `user`. **Filterable.** |
| `owner_id` | – | Cognito `sub` when `scope=user`. **Filterable.** |
| `list_id` | – | User list id when `scope=user`. **Filterable.** |

## How metadata is stored in S3 Vectors

S3 Vectors splits metadata into two kinds, and the split is **fixed at index creation**
(`infrastructure/bootstrap_s3vectors.py`):

- **Filterable** (small; usable in query filters): `domain`, `subfield`, `difficulty_tier`,
  `cefr_level`, `scope`, `owner_id`, `list_id`.
- **Non-filterable** (larger text; returned with results via `returnMetadata`): `term`,
  `definition`, `example`, `synonyms`, `pos`, `source`.

The vector **key** is `term_id`; vector data is `float32`, dimension 1024, cosine distance.

## Example

```json
{"term_id":"ai_rag_001","term":"retrieval-augmented generation","definition":"A technique that grounds a language model's output in documents retrieved at query time.","example":"The assistant uses retrieval-augmented generation to cite its sources.","synonyms":["RAG"],"domain":"ai","subfield":"llm_architecture","cefr_level":"C1","pos":"noun"}
```

## Corpus sourcing (Q-n)

The global corpus is seeded from the **CEFR-J Vocabulary Profile** (A1–B2, free for research and
commercial use with citation © Tono Lab, TUFS) plus the **Octanove C1/C2** profile (CC BY-SA 4.0).
Neither contains definitions, so **Bedrock generates definitions/examples/synonyms** for each
headword. A derived corpus file should carry CC BY-SA 4.0 + the TUFS citation if distributed.
