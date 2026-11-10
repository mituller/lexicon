"""Pronunciation audio with Amazon Polly (neural voice), returned as base64 MP3."""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Any

import boto3

from .config import get_settings

VOICE_ID = "Joanna"


@lru_cache(maxsize=1)
def _polly() -> Any:
    return boto3.client("polly", region_name=get_settings().aws_region)


@lru_cache(maxsize=512)
def pronounce(word: str) -> str:
    """Base64 MP3 of ``word`` (cached per warm Lambda)."""
    response = _polly().synthesize_speech(
        Text=word, OutputFormat="mp3", VoiceId=VOICE_ID, Engine="neural"
    )
    return base64.b64encode(response["AudioStream"].read()).decode("ascii")
