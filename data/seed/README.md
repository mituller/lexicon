# Vocabulary sources

The vocabulary is no longer a static seed file. It grows on demand:

1. **`python -m lexicon sync-sources`** fetches the online word lists, scores each headword's
   difficulty (0–100) from its source plus [Datamuse](https://www.datamuse.com/api/) word
   frequency, then has Nova Pro rate them in batches of 50 on an anchored scale
   (`difficulty.DIFFICULTY_SCALE`) and reject junk and derived forms ("hauntingly"). New words
   are parked in the DynamoDB candidate pool. Frequency alone mistakes "rare" for "hard"; the
   rating puts pooled scores on the same scale as published ones.
2. **Refill** (`python -m lexicon refill --lo 60 --hi 70`, or automatically from the API when a
   learner's band runs low) has Claude Sonnet 4.6 write the entry — definition, three examples,
   synonyms, commonly confused words, etymology, its own difficulty estimate (Nova Lite/Pro
   invented etymologies in testing; set `LEXICON_ENRICH_MODEL_ID` to trade that off) — then
   embeds it with Titan
   and writes it to S3 Vectors and the DynamoDB term index. Proper nouns and junk are rejected.

3. **Bedrock suggestions** fill the hard end, where the curated lists run thin (after rating,
   only ~13 of ~2,350 pooled words score 70+). When refill finds too few candidates in a band, Nova Lite suggests
   words at that difficulty, calibrated with nearby published words. Each suggestion must be an
   exact Datamuse match with at least 0.002 occurrences per million words, which drops invented
   words but keeps real rare ones. Run it by hand with
   `python -m lexicon suggest --lo 80 --hi 90 [--seed lugubrious ...]`.

Code: `python/lexicon/sources.py`, `difficulty.py`, `enrichment.py`, `termindex.py`, `refill.py`, `suggest.py`, `rating.py`.

## Default sources

| Source | Words | License |
|---|---|---|
| [Octanove Vocabulary Profile C1/C2](https://github.com/openlanguageprofiles/olp-en-cefrj) | ~2,090 | CC BY-SA 4.0 |
| [New Academic Word List 1.2](https://www.newgeneralservicelist.com/new-academic-word-list) — Browne, C., Culligan, B., & Phillips, J. (2013) | ~957 | CC BY-SA 4.0 |

Add any one-word-per-line list you have rights to: `sync-sources --list gre=./gre.txt@80`
(`name=path-or-url@baseline-difficulty`). If you distribute the derived corpus, keep the
CC BY-SA 4.0 attribution above.

The `*.jsonl` files here are the old static corpus and will be removed with the deck/quiz code.
