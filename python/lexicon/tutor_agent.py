"""RAG orchestration: retrieved vocabulary + user request -> grounded tutor response.

The tutor is an expert language and technical tutor. It must ground answers in the retrieved
context and never invent vocabulary outside it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from enum import StrEnum

from .bedrock import BedrockClient
from .models import SearchHit


class TutorMode(StrEnum):
    EXPLAIN = "explain"
    QUIZ = "quiz"
    COMPARE = "compare"


SYSTEM_PROMPT = """You are Lexicon, an expert English language and technical vocabulary tutor.

Rules:
- Ground every claim in the provided CONTEXT. If the answer is not supported by the context,
  say so and offer the closest relevant term instead of inventing one.
- Teach, don't just define: give the definition, register/nuance, a natural example, and a
  common mistake or collocation when useful.
- Match the learner's CEFR level when one is provided; keep explanations simple for low levels.
- Be concise and structured. Use short headings or bullets, not walls of text."""


def format_context(hits: Sequence[SearchHit]) -> str:
    """Render retrieved terms as a compact, citable context block."""
    if not hits:
        return "(no matching vocabulary found)"
    lines: list[str] = []
    for hit in hits:
        synonyms = f" | synonyms: {', '.join(hit.synonyms)}" if hit.synonyms else ""
        lines.append(
            f"- [{hit.term_id}] **{hit.term}** ({hit.cefr_level}, {hit.difficulty_tier}, "
            f"{hit.domain}/{hit.subfield or 'general'}){synonyms}\n"
            f"  definition: {hit.definition}\n"
            f"  example: {hit.example}"
        )
    return "\n".join(lines)


def build_prompt(query: str, hits: Sequence[SearchHit], *, mode: TutorMode = TutorMode.EXPLAIN) -> str:
    """Build the user prompt for the tutor model."""
    task = {
        TutorMode.EXPLAIN: "Explain the following request using the vocabulary below.",
        TutorMode.QUIZ: (
            "Create a short practice quiz (3-5 questions) that tests the vocabulary below. "
            "Include the answer key at the end."
        ),
        TutorMode.COMPARE: (
            "Compare and contrast the vocabulary below, focusing on nuance and when to use each."
        ),
    }[mode]
    return (
        f"{task}\n\n"
        f"CONTEXT:\n{format_context(hits)}\n\n"
        f"LEARNER REQUEST: {query.strip()}"
    )


class TutorAgent:
    """Turns a user request plus retrieved context into a tutor response."""

    def __init__(self, bedrock: BedrockClient | None = None) -> None:
        self._bedrock = bedrock or BedrockClient()

    def respond(
        self,
        query: str,
        hits: Sequence[SearchHit],
        *,
        mode: TutorMode = TutorMode.EXPLAIN,
    ) -> str:
        prompt = build_prompt(query, hits, mode=mode)
        return self._bedrock.generate(prompt, system=SYSTEM_PROMPT)

    def stream(
        self,
        query: str,
        hits: Sequence[SearchHit],
        *,
        mode: TutorMode = TutorMode.EXPLAIN,
    ) -> Iterator[str]:
        """Stream text deltas for fast time-to-first-token in the UI."""
        prompt = build_prompt(query, hits, mode=mode)
        yield from self._bedrock.generate_stream(prompt, system=SYSTEM_PROMPT)
