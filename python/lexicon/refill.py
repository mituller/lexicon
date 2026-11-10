"""On-demand vocabulary growth: sources -> candidate pool -> Bedrock -> S3 Vectors + term index.

Two stages, so the expensive one only runs when a learner needs words:

1. :meth:`VocabularyPool.sync_sources` (cheap, CLI/scheduled): fetch the online word lists, score
   each headword's difficulty from source + Datamuse frequency, and park new ones in the
   candidate pool. The enrichment model then rates them in batches of ~50 on the anchored
   difficulty scale and flags junk (``rating.py``): about one call per 50 words.
2. :meth:`VocabularyPool.ensure_band` (on demand, API): if fewer than ``min_available`` enriched
   terms the learner hasn't seen sit in a difficulty band, enrich the next candidates from that
   band with Bedrock, embed them with Titan, and write them to S3 Vectors and the term index.
   When the curated lists have nothing left in the band, Nova Lite suggests new words first
   (:meth:`VocabularyPool.suggest`, see ``suggest.py``).
"""

from __future__ import annotations

import logging
import random
from collections.abc import Collection, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .bedrock import BedrockClient
from .difficulty import DatamuseFrequency, FrequencyLookup, cefr_for, combine, derived_from
from .enrichment import Enrichment, enrich
from .logging_setup import json_event
from .models import TermRecord
from .rating import BatchRater
from .sources import Candidate, WordSource, default_sources, merge_candidates
from .suggest import MAX_EXCLUDES, MAX_SEEDS, BedrockSuggestionSource
from .termindex import TermIndex, term_id_for
from .vectorstore import VectorStore

logger = logging.getLogger("lexicon")

# Bedrock calls per refill run in parallel; Nova Lite takes ~2-3 s each.
ENRICH_WORKERS = 4


@dataclass
class SyncReport:
    fetched: int = 0
    already_known: int = 0
    added: int = 0
    rejected: int = 0
    rated: int = 0
    by_source: dict[str, int] = field(default_factory=dict)


@dataclass
class RefillReport:
    requested: int = 0
    added: list[TermRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    suggested: int = 0


class VocabularyPool:
    def __init__(
        self,
        *,
        index: TermIndex | None = None,
        store: VectorStore | None = None,
        bedrock: BedrockClient | None = None,
        frequency: FrequencyLookup | None = None,
        rater: BatchRater | None = None,
        domain: str = "general",
    ) -> None:
        self._index = index or TermIndex()
        self._store = store or VectorStore()
        self._bedrock = bedrock or BedrockClient()
        self._frequency = frequency or DatamuseFrequency()
        self._rater = rater or BatchRater(self._bedrock)
        self.domain = domain

    # --- stage 1: sources -> candidate pool ---

    def sync_sources(
        self, sources: Sequence[WordSource] | None = None, *, rate: bool = True
    ) -> SyncReport:
        report = SyncReport()
        batches = []
        for source in sources or default_sources():
            fetched = source.fetch()
            report.by_source[source.name] = len(fetched)
            batches.append(fetched)
        candidates = merge_candidates(batches)
        report.fetched = len(candidates)

        pooled = self._index.all_candidate_headwords(self.domain)
        ids = {c.headword.lower(): term_id_for(c.headword, self.domain) for c in candidates}
        known = self._index.known_ids(list(ids.values()))
        fresh = [
            c
            for c in candidates
            if c.headword.lower() not in pooled and ids[c.headword.lower()] not in known
        ]
        report.already_known = len(candidates) - len(fresh)

        frequencies = self._lookup_frequencies([c.headword for c in fresh])
        ratings = self._rater.rate([c.headword for c in fresh]) if rate and fresh else {}
        scored: list[tuple[Candidate, int]] = []
        rejected: list[tuple[str, str, str]] = []
        for c in fresh:
            score = combine(c.base_difficulty, frequencies.get(c.headword.lower()))
            rating = ratings.get(c.headword.lower())
            if rating is not None and rating.skip:
                rejected.append((ids[c.headword.lower()], c.headword, rating.reason or "rated skip"))
                continue
            if rating is not None and rating.difficulty is not None:
                # Same weighting as build_term, so pooled and published scores agree.
                score = round(0.25 * score + 0.75 * rating.difficulty)
                report.rated += 1
            scored.append((c, score))
        self._index.mark_skipped_many(rejected)
        report.rejected = len(rejected)
        report.added = self._index.put_candidates(scored, self.domain)
        return report

    def _lookup_frequencies(self, words: Sequence[str]) -> dict[str, float | None]:
        many = getattr(self._frequency, "many", None)
        if callable(many):
            result: dict[str, float | None] = many(words)
            return result
        return {w.lower(): self._frequency.per_million(w) for w in words}

    def suggest(self, lo: int, hi: int, *, count: int, seeds: Sequence[str] | None = None) -> SyncReport:
        """Ask Bedrock for new words in ``[lo, hi)`` and add the verified ones to the pool.

        ``seeds`` are words at about this level (the session engine passes ones the learner has
        mastered); by default the closest published terms are used.
        """
        target = (lo + hi) // 2
        if seeds is None:
            nearby = self._index.terms_in_band(max(0, lo - 10), hi + 10, domain=self.domain)
            nearby.sort(key=lambda term: abs(term.difficulty - target))
            seeds = [term.term for term in nearby[:MAX_SEEDS]]
        exclude = [
            str(item["headword"])
            for item in self._index.candidates_in_band(lo, hi, domain=self.domain, limit=MAX_EXCLUDES)
        ]
        source = BedrockSuggestionSource(
            seeds=seeds,
            difficulty=target,
            count=count,
            exclude=exclude,
            frequency=self._frequency,
            bedrock=self._bedrock,
        )
        report = self.sync_sources([source])
        logger.info(
            json_event("suggest", lo=lo, hi=hi, added=report.added, rejected=len(source.rejected))
        )
        return report

    # --- stage 2: on-demand enrichment ---

    def ensure_band(
        self,
        lo: int,
        hi: int,
        *,
        exclude_ids: Collection[str] = (),
        min_available: int = 20,
        batch_size: int = 8,
        seeds: Sequence[str] | None = None,
    ) -> RefillReport:
        """Top up ``[lo, hi)`` so at least ``min_available`` unseen terms exist there."""
        available = sum(
            1
            for term in self._index.terms_in_band(lo, hi, domain=self.domain)
            if term.term_id not in exclude_ids
        )
        if available >= min_available:
            return RefillReport()
        return self.refill(lo, hi, count=min(batch_size, min_available - available), seeds=seeds)

    def refill(
        self,
        lo: int,
        hi: int,
        *,
        count: int,
        seeds: Sequence[str] | None = None,
        allow_suggest: bool = True,
    ) -> RefillReport:
        """Enrich up to ``count`` candidates from ``[lo, hi)`` and publish them.

        If the band has fewer than ``count`` candidates, Bedrock suggests more first. It is asked
        for twice as many as needed because some fail verification and some score outside the
        band once frequency is known (those stay pooled for their own band).
        """
        report = RefillReport()
        picked = self._pick(lo, hi, count)
        if len(picked) < count and allow_suggest:
            try:
                report.suggested = self.suggest(lo, hi, count=max(10, 2 * count), seeds=seeds).added
            except Exception as error:  # noqa: BLE001 - still enrich whatever is pooled
                logger.warning(json_event("suggest_failed", lo=lo, hi=hi, error=str(error)))
            else:
                picked = self._pick(lo, hi, count)
        picked = self._drop_derivations(picked, report)[:count]
        report.requested = len(picked)
        if not picked:
            logger.info(json_event("candidate_pool_empty", lo=lo, hi=hi, domain=self.domain))
            return report

        with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
            results = list(pool.map(self._enrich_one, picked))

        ready: list[TermRecord] = []
        for item, result in zip(picked, results, strict=True):
            headword = str(item["headword"])
            term_id = term_id_for(headword, self.domain)
            if isinstance(result, Exception):
                logger.warning(json_event("enrich_failed", term=headword, error=str(result)))
                report.failed.append(headword)
                continue  # leave it in the pool; a later refill retries
            if result.skip:
                self._index.mark_skipped(term_id, headword, result.skip_reason)
                self._index.remove_candidate(item)
                report.skipped.append(headword)
                continue
            ready.append(build_term(item, result, domain=self.domain))

        if ready:
            embeddings = [self._bedrock.embed(t.to_vocabulary_term().embedding_text()) for t in ready]
            self._store.put_terms([t.to_vocabulary_term() for t in ready], embeddings)
            by_headword = {str(i["headword"]).lower(): i for i in picked}
            for term in ready:
                # Index after the vector write so a listed term is always searchable.
                self._index.put_term(term)
                self._index.remove_candidate(by_headword[term.term.lower()])
            report.added = ready
        logger.info(
            json_event(
                "refill",
                lo=lo,
                hi=hi,
                added=len(report.added),
                skipped=len(report.skipped),
                failed=len(report.failed),
            )
        )
        return report

    def publish_headwords(
        self, headwords: Sequence[str], *, source: str, base_difficulty: int = 60
    ) -> tuple[list[TermRecord], list[str]]:
        """Make specific words studyable now (words a learner imported from their own reading).

        Returns ``(terms, rejected)``: already-published words are reused as they are; new ones
        are enriched, embedded and published like a refill; words the model refuses (or refused
        before) come back in ``rejected``.
        """
        unique = list({h.strip().lower(): h.strip() for h in headwords if h.strip()}.values())
        ids = {h: term_id_for(h, self.domain) for h in unique}
        existing = self._index.get_terms(list(ids.values()))
        known = self._index.known_ids(list(ids.values()))
        terms = [existing[ids[h]] for h in unique if ids[h] in existing]
        rejected = [h for h in unique if ids[h] in known and ids[h] not in existing]
        fresh = [h for h in unique if ids[h] not in known]
        if not fresh:
            return terms, rejected

        frequencies = self._lookup_frequencies(fresh)
        items = [
            {
                "headword": h,
                "pos": "",
                "source": source,
                "cefr_level": "",
                "difficulty": combine(base_difficulty, frequencies.get(h.lower())),
            }
            for h in fresh
        ]
        with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
            results = list(pool.map(self._enrich_one, items))
        ready: list[TermRecord] = []
        for item, result in zip(items, results, strict=True):
            headword = str(item["headword"])
            if isinstance(result, Exception):
                logger.warning(json_event("enrich_failed", term=headword, error=str(result)))
                rejected.append(headword)
            elif result.skip:
                self._index.mark_skipped(ids[headword], headword, result.skip_reason)
                rejected.append(headword)
            else:
                ready.append(build_term(item, result, domain=self.domain))
        if ready:
            embeddings = [self._bedrock.embed(t.to_vocabulary_term().embedding_text()) for t in ready]
            self._store.put_terms([t.to_vocabulary_term() for t in ready], embeddings)
            for term in ready:
                self._index.put_term(term)
        return terms + ready, rejected

    def _pick(self, lo: int, hi: int, count: int) -> list[dict[str, Any]]:
        """A random sample across the band (key order would always favour "a…" words).

        Oversamples so that dropping derived forms still leaves ``count`` words.
        """
        pool = self._index.candidates_in_band(lo, hi, domain=self.domain)
        return random.sample(pool, k=min(2 * count, len(pool)))

    def _drop_derivations(
        self, picked: list[dict[str, Any]], report: RefillReport
    ) -> list[dict[str, Any]]:
        """Skip words like "bereftly" without a Bedrock call; the model doesn't reliably refuse them."""
        with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
            bases = list(pool.map(lambda item: derived_from(str(item["headword"]), self._frequency), picked))
        kept: list[dict[str, Any]] = []
        for item, base in zip(picked, bases, strict=True):
            headword = str(item["headword"])
            if base is None:
                kept.append(item)
                continue
            self._index.mark_skipped(term_id_for(headword, self.domain), headword, f"derived from {base}")
            self._index.remove_candidate(item)
            report.skipped.append(headword)
        return kept

    def _enrich_one(self, item: dict[str, Any]) -> Enrichment | Exception:
        try:
            return enrich(str(item["headword"]), pos=str(item.get("pos", "")), bedrock=self._bedrock)
        except Exception as error:  # noqa: BLE001 - one bad word must not sink the batch
            return error


def build_term(item: dict[str, Any], enrichment: Enrichment, *, domain: str) -> TermRecord:
    """Merge a pooled candidate with its enrichment; the model's estimate refines difficulty."""
    headword = str(item["headword"])
    pooled = int(item["difficulty"])
    # With the anchored scale (difficulty.DIFFICULTY_SCALE) the model ranks advanced words far
    # better than frequency, which squeezes them all into ~60-90 ("discombobulate" above
    # "antediluvian"), so the pooled score is only a tiebreaker.
    difficulty = (
        round(0.25 * pooled + 0.75 * enrichment.difficulty)
        if enrichment.difficulty is not None
        else pooled
    )
    return TermRecord(
        term_id=term_id_for(headword, domain),
        term=headword,
        domain=domain,
        pos=enrichment.pos or str(item.get("pos", "")),
        definition=enrichment.definition,
        examples=enrichment.examples,
        synonyms=enrichment.synonyms,
        confused_with=enrichment.confused_with,
        etymology=enrichment.etymology,
        subfield=enrichment.subfield,
        cefr_level=str(item.get("cefr_level") or "") or cefr_for(difficulty),
        difficulty=difficulty,
        source=str(item.get("source", "")),
    )

