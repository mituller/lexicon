"""Amazon Bedrock access: Titan Text Embeddings V2 (embeddings) and Nova Lite (generation).

Clients are cached at module scope so Lambda warm invocations skip client construction. All
Bedrock calls in the app go through this module.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import get_settings

_RETRY_CONFIG = Config(retries={"max_attempts": 5, "mode": "standard"})


@lru_cache(maxsize=1)
def _runtime_client() -> Any:
    """Cached ``bedrock-runtime`` client (never build one per request)."""
    return boto3.client(
        "bedrock-runtime",
        region_name=get_settings().aws_region,
        config=_RETRY_CONFIG,
    )


class BedrockClient:
    """Thin wrapper around the Bedrock Runtime API."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client or _runtime_client()

    # --- Embeddings ---

    def embed(self, text: str, *, dimensions: int | None = None) -> list[float]:
        """Embed ``text`` with Titan Text Embeddings V2.

        Vectors are normalized so cosine distance behaves as expected.
        """
        settings = get_settings()
        body: dict[str, Any] = {
            "inputText": text,
            "normalize": True,
            "dimensions": dimensions or settings.embedding_dimensions,
        }
        response = self._client.invoke_model(
            modelId=settings.embed_model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
        payload = json.loads(response["body"].read())
        embedding: list[float] = payload["embedding"]
        return embedding

    # --- Generation ---

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model_id: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Generate a completion (non-streaming); defaults to the configured generation model."""
        settings = get_settings()
        messages: list[dict[str, Any]] = [{"role": "user", "content": [{"text": prompt}]}]
        request: dict[str, Any] = {
            "modelId": model_id or settings.generation_model_id,
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": max_tokens or settings.generation_max_tokens,
                "temperature": settings.generation_temperature,
            },
        }
        if system:
            request["system"] = [{"text": system}]
        response = self._client.converse(**request)
        return _extract_text(response)

    def generate_stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        """Stream a completion with Nova Lite, yielding text deltas."""
        settings = get_settings()
        messages: list[dict[str, Any]] = [{"role": "user", "content": [{"text": prompt}]}]
        request: dict[str, Any] = {
            "modelId": settings.generation_model_id,
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": settings.generation_max_tokens,
                "temperature": settings.generation_temperature,
            },
        }
        if system:
            request["system"] = [{"text": system}]
        response = self._client.converse_stream(**request)
        for event in response.get("stream", []):
            delta = event.get("contentBlockDelta")
            if delta and (text := delta.get("delta", {}).get("text")):
                yield text


def _extract_text(response: dict[str, Any]) -> str:
    """Pull concatenated text out of a Bedrock Converse response."""
    try:
        blocks = response["output"]["message"]["content"]
    except (KeyError, TypeError):  # pragma: no cover - defensive
        return ""
    return "".join(block.get("text", "") for block in blocks)


def is_throttled(error: ClientError) -> bool:
    """Whether a Bedrock error is transient throttling."""
    code = error.response.get("Error", {}).get("Code", "")
    return code in {"ThrottlingException", "TooManyRequestsException", "ServiceQuotaExceededException"}
