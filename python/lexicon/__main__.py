"""Offline CLI: ``python -m lexicon <command>``.

Commands:
    init     Create the S3 Vectors bucket + index if missing.
    seed     Embed and upsert vocabulary records from a JSON Lines file.
    search   Run a filtered semantic search.
    ask      Run a full RAG tutor turn.
    sync-sources  Fetch online word lists into the candidate pool.
    refill   Enrich candidates in a difficulty band (Bedrock) and publish them.
    suggest  Ask Bedrock for new words in a band (verified against Datamuse).
    pool     Show enriched terms and pending candidates per difficulty band.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .ingestion import ingest_records, iter_jsonl
from .refill import VocabularyPool
from .retriever import Retriever
from .sources import TextListSource, WordSource, default_sources
from .termindex import TermIndex
from .tutor_agent import TutorAgent, TutorMode
from .vectorstore import VectorStore


def _cmd_init(args: argparse.Namespace) -> int:
    VectorStore().ensure_index()
    print(f"vector index ready: {args.bucket or '(default bucket)'}")
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    path = Path(args.file)
    records = list(iter_jsonl(path))
    written = ingest_records(records, source=args.source)
    print(f"embedded and upserted {written} records from {path}")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    hits = Retriever().search(
        args.query,
        domains=args.domain or None,
        difficulty_tiers=args.tier or None,
        cefr_levels=args.cefr or None,
        top_k=args.top_k,
    )
    for hit in hits:
        print(f"[{hit.cefr_level}/{hit.difficulty_tier}] {hit.term} — {hit.definition}")
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    hits = Retriever().search(
        args.query,
        domains=args.domain or None,
        difficulty_tiers=args.tier or None,
        cefr_levels=args.cefr or None,
        top_k=args.top_k,
    )
    answer = TutorAgent().respond(args.query, hits, mode=TutorMode(args.mode))
    print(answer)
    return 0


def _cmd_sync_sources(args: argparse.Namespace) -> int:
    sources: list[WordSource] = [] if args.no_defaults else list(default_sources())
    for spec in args.list or []:
        name, _, rest = spec.partition("=")
        location, _, base = rest.rpartition("@")
        sources.append(TextListSource(name, location, base_difficulty=int(base)))
    report = VocabularyPool(domain=args.domain).sync_sources(sources)
    for name, count in report.by_source.items():
        print(f"  {name}: {count} headwords")
    print(
        f"{report.fetched} unique, {report.already_known} already known, "
        f"{report.rejected} rejected by the rater, {report.added} added to the candidate pool "
        f"({report.rated} model-rated)"
    )
    return 0


def _cmd_refill(args: argparse.Namespace) -> int:
    report = VocabularyPool(domain=args.domain).refill(
        args.lo, args.hi, count=args.count, allow_suggest=not args.no_suggest
    )
    if report.suggested:
        print(f"  Bedrock suggested {report.suggested} new words for this band")
    for term in report.added:
        print(f"  + [{term.difficulty:3d}] {term.term} — {term.definition}")
    for word in report.skipped:
        print(f"  - skipped {word}")
    for word in report.failed:
        print(f"  ! failed {word}")
    print(f"added {len(report.added)} of {report.requested} candidates")
    return 0


def _cmd_suggest(args: argparse.Namespace) -> int:
    report = VocabularyPool(domain=args.domain).suggest(
        args.lo, args.hi, count=args.count, seeds=args.seed
    )
    print(f"{report.fetched} verified suggestions, {report.added} added to the candidate pool")
    return 0


def _cmd_pool(args: argparse.Namespace) -> int:
    index = TermIndex()
    print(f"{'band':>9}  {'terms':>5}  {'candidates':>10}")
    for lo in range(0, 100, args.step):
        hi = lo + args.step
        terms = len(index.terms_in_band(lo, hi, domain=args.domain))
        pending = len(index.candidates_in_band(lo, hi, domain=args.domain))
        if terms or pending:
            print(f"{lo:3d}–{hi:<3d}    {terms:5d}  {pending:10d}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lexicon", description="RAG vocabulary tutor runtime")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create the S3 Vectors bucket + index")
    init.add_argument("--bucket", default="")
    init.set_defaults(func=_cmd_init)

    seed = sub.add_parser("seed", help="ingest vocabulary records")
    seed.add_argument("file", help="JSON Lines file of vocabulary records")
    seed.add_argument("--source", default="cefr-j")
    seed.set_defaults(func=_cmd_seed)

    search = sub.add_parser("search", help="filtered semantic search")
    search.add_argument("query")
    search.add_argument("--domain", action="append")
    search.add_argument("--tier", action="append")
    search.add_argument("--cefr", action="append")
    search.add_argument("--top-k", type=int, default=8)
    search.set_defaults(func=_cmd_search)

    ask = sub.add_parser("ask", help="full RAG tutor turn")
    ask.add_argument("query")
    ask.add_argument("--mode", default="explain", choices=[m.value for m in TutorMode])
    ask.add_argument("--domain", action="append", help="restrict retrieval to a domain")
    ask.add_argument("--tier", action="append", help="restrict to a difficulty tier")
    ask.add_argument("--cefr", action="append", help="restrict to CEFR levels")
    ask.add_argument("--top-k", type=int, default=8)
    ask.set_defaults(func=_cmd_ask)

    sync = sub.add_parser(
        "sync-sources", help="fetch online word lists into the candidate pool (no Bedrock)"
    )
    sync.add_argument("--domain", default="general")
    sync.add_argument(
        "--list",
        action="append",
        metavar="NAME=URL_OR_PATH@BASE",
        help="extra one-word-per-line list with a 0-100 baseline, e.g. gre=./gre.txt@80",
    )
    sync.add_argument("--no-defaults", action="store_true", help="skip Octanove + NAWL")
    sync.set_defaults(func=_cmd_sync_sources)

    refill = sub.add_parser("refill", help="enrich candidates in a difficulty band via Bedrock")
    refill.add_argument("--lo", type=int, required=True)
    refill.add_argument("--hi", type=int, required=True)
    refill.add_argument("--count", type=int, default=8)
    refill.add_argument("--domain", default="general")
    refill.add_argument(
        "--no-suggest",
        action="store_true",
        help="only use pooled candidates; don't ask Bedrock for new words",
    )
    refill.set_defaults(func=_cmd_refill)

    suggest = sub.add_parser("suggest", help="ask Bedrock for new words in a difficulty band")
    suggest.add_argument("--lo", type=int, required=True)
    suggest.add_argument("--hi", type=int, required=True)
    suggest.add_argument("--count", type=int, default=15)
    suggest.add_argument("--seed", action="append", help="calibration word (repeatable)")
    suggest.add_argument("--domain", default="general")
    suggest.set_defaults(func=_cmd_suggest)

    pool = sub.add_parser("pool", help="show terms and pending candidates per difficulty band")
    pool.add_argument("--domain", default="general")
    pool.add_argument("--step", type=int, default=10)
    pool.set_defaults(func=_cmd_pool)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as error:  # noqa: BLE001 - top-level CLI guard
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
