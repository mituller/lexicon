"""Offline CLI: ``python -m lexicon <command>``.

Commands:
    init     Create the S3 Vectors bucket + index if missing.
    seed     Embed and upsert vocabulary records from a JSON Lines file.
    search   Run a filtered semantic search.
    ask      Run a full RAG tutor turn.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

from .corpus import (
    CefrEntry,
    DomainDetails,
    TermDetails,
    build_corpus,
    build_domain_corpus,
    generate_details,
    generate_domain_details,
    parse_cefr_csv,
    parse_term_list,
    write_jsonl,
)
from .ingestion import ingest_records, iter_jsonl
from .retriever import Retriever
from .tutor_agent import TutorAgent, TutorMode
from .vectorstore import VectorStore

DEFAULT_CEFRJ_URL = (
    "https://raw.githubusercontent.com/openlanguageprofiles/olp-en-cefrj/master/"
    "cefrj-vocabulary-profile-1.5.csv"
)
DEFAULT_OCTANOVE_URL = (
    "https://raw.githubusercontent.com/openlanguageprofiles/olp-en-cefrj/master/"
    "octanove-vocabulary-profile-c1c2-1.0.csv"
)


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


def read_text(source: str) -> str:
    """Read a local path or an http(s) URL."""
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=30) as response:  # noqa: S310
            return bytes(response.read()).decode("utf-8-sig")
    return Path(source).read_text(encoding="utf-8-sig")


def _cmd_build_corpus(args: argparse.Namespace) -> int:
    entries: list[CefrEntry] = []
    for source in args.input:
        entries.extend(parse_cefr_csv(read_text(source)))
    print(f"parsed {len(entries)} CEFR-J entries")

    def generate(entry: CefrEntry) -> TermDetails:
        if args.no_generate:
            return TermDetails(definition="(pending)", example="", synonyms=[])
        return generate_details(entry)

    records = build_corpus(
        entries,
        generate,
        domain=args.domain,
        source="cefr-j",
        limit_per_level=args.limit_per_level,
        on_progress=lambda done, total: print(f"  enriched {done}/{total}") if done % 25 == 0 else None,
    )
    written = write_jsonl(records, args.out)
    print(f"wrote {written} records to {args.out}")
    return 0


def _cmd_build_domain_corpus(args: argparse.Namespace) -> int:
    terms = parse_term_list(read_text(args.terms))
    print(f"parsed {len(terms)} {args.domain} terms")

    def generate(term: str) -> DomainDetails:
        if args.no_generate:
            return DomainDetails(definition="(pending)", cefr_level="B2", subfield=args.domain)
        return generate_domain_details(term, args.domain)

    records = build_domain_corpus(
        terms,
        generate,
        domain=args.domain,
        on_progress=lambda done, total: print(f"  enriched {done}/{total}") if done % 25 == 0 else None,
    )
    written = write_jsonl(records, args.out)
    print(f"wrote {written} records to {args.out}")
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

    build = sub.add_parser("build-corpus", help="CEFR-J word lists -> Bedrock-enriched JSONL")
    build.add_argument(
        "--input",
        action="append",
        default=None,
        help="CSV path or URL (repeatable). Defaults to the CEFR-J + Octanove URLs.",
    )
    build.add_argument("--out", default="../data/seed/generated.jsonl")
    build.add_argument("--domain", default="general")
    build.add_argument("--limit-per-level", type=int, default=None)
    build.add_argument(
        "--no-generate",
        action="store_true",
        help="Skip Bedrock (placeholder definitions); useful to validate offline.",
    )
    build.set_defaults(func=_cmd_build_corpus)

    domain = sub.add_parser(
        "build-domain-corpus", help="technical term list -> Bedrock-enriched JSONL"
    )
    domain.add_argument("--terms", required=True, help="text file, one term per line")
    domain.add_argument("--domain", default="ai")
    domain.add_argument("--out", required=True)
    domain.add_argument("--no-generate", action="store_true")
    domain.set_defaults(func=_cmd_build_domain_corpus)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "input", None) is None and getattr(args, "func", None) is _cmd_build_corpus:
        args.input = [DEFAULT_CEFRJ_URL, DEFAULT_OCTANOVE_URL]
    try:
        return int(args.func(args))
    except Exception as error:  # noqa: BLE001 - top-level CLI guard
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
