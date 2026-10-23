"""Runtime configuration.

All values come from environment variables prefixed with ``LEXICON_`` (or a local ``.env``).
The Lambda deployment sets these via the CDK stack; local CLI runs read a ``.env`` file.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(
        env_prefix="LEXICON_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- AWS ---
    aws_region: str = "us-east-1"

    # --- S3 Vectors ---
    vector_bucket: str = "lexicon-vectors"
    vector_index: str = "vocabulary"
    embedding_dimensions: int = 1024

    # --- Bedrock ---
    embed_model_id: str = "amazon.titan-embed-text-v2:0"
    generation_model_id: str = "us.amazon.nova-lite-v1:0"
    generation_max_tokens: int = 1024
    generation_temperature: float = 0.3
    # Writes the stored dictionary entries, once per word. Claude Sonnet 4.6 was the only model
    # tested whose etymologies were reliable (Nova Lite/Pro invent them); empty = generation model.
    enrich_model_id: str = "us.anthropic.claude-sonnet-4-6"
    # Batch-rates pooled words (~50 per call). Nova Pro ranks them almost exactly like Sonnet
    # (rank correlation 0.95 on a 50-word sample) at ~1/5 the cost.
    rate_model_id: str = "us.amazon.nova-pro-v1:0"

    # --- DynamoDB ---
    dynamodb_table: str = "lexicon"

    # --- Cognito (auth) ---
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_domain: str = ""
    cognito_callback_url: str = "http://127.0.0.1:4200/callback"

    # --- Web/CORS ---
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "tauri://localhost",
            "https://tauri.localhost",
        ]
    )

    @property
    def cognito_issuer(self) -> str:
        """OIDC issuer URL used to validate JWTs and fetch JWKS."""
        return (
            f"https://cognito-idp.{self.aws_region}.amazonaws.com/{self.cognito_user_pool_id}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
