# Seed corpus

`samples.jsonl` shows the record shape consumed by `python -m lexicon seed`.

## Building the full corpus (planned)

1. **Word list + CEFR levels** — download the CEFR-J Vocabulary Profile and the Octanove C1/C2
   profile from [`openlanguageprofiles/olp-en-cefrj`](https://github.com/openlanguageprofiles/olp-en-cefrj).
   Take a curated subset per level/domain (~300–500 general + ~150–250 AI/technical for v1).
2. **Definitions/examples/synonyms** — generate with Bedrock (Nova Lite) using a strict prompt;
   review/spot-check output.
3. **Emit JSONL** in the schema from `../schema.md`, then `python -m lexicon seed`.

Generate it with the CLI (implemented in `python/lexicon/corpus.py`):

```bash
# Full run: downloads CEFR-J + Octanove, enriches each word via Bedrock
python -m lexicon build-corpus --out data/seed/generated.jsonl --limit-per-level 60

# Offline dry run (placeholder definitions, no Bedrock) to validate the pipeline
python -m lexicon build-corpus --out /tmp/dryrun.jsonl --no-generate --limit-per-level 5
```

Attribution to Tono Lab (TUFS) is required; the derived file is CC BY-SA 4.0 if distributed.
