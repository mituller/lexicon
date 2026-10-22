"""Structured-ish logging setup for the Lambda runtime.

CloudWatch picks up stdout, so we log JSON lines: request summaries, Bedrock latency, and
errors. Kept dependency-free (standard library ``logging`` only).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

LOGGER_NAME = "lexicon"


def json_event(event: str, **fields: object) -> str:
    """Format a single structured log line."""
    return json.dumps({"event": event, **fields})


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the root logger and return the app logger."""
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(handler)
    return logging.getLogger(LOGGER_NAME)


def install_request_logging(app: FastAPI) -> None:
    """Add middleware that logs one JSON line per request (no query/body contents)."""
    logger = logging.getLogger(LOGGER_NAME)

    @app.middleware("http")
    async def _log_request(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            logger.info(
                json.dumps(
                    {
                        "event": "request",
                        "method": request.method,
                        "path": request.url.path,
                        "status": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                )
            )
