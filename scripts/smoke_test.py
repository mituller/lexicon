#!/usr/bin/env python3
"""Lexicon smoke test: verify each setup stage without changing anything.

Read-only: checks your AWS identity, Bedrock model access, S3 Vectors bucket/index, the
DynamoDB table, Cognito (if configured), the deployed API health endpoint, and whether the
corpus has been seeded. Nothing is created, deleted, or modified.

Run it with the project venv so boto3 and pydantic-settings are available:

    python/.venv/bin/python scripts/smoke_test.py

Exit code is 0 when there are no failures (warnings/skips are allowed).

Companion checklist: docs/smoke-test.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

# Load python/.env explicitly so the script works from any directory (pydantic-settings would
# otherwise resolve `.env` against the current working directory).
def _load_dotenv() -> None:
    env_file = ROOT / "python" / ".env"
    if not env_file.is_file():
        return
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - dotenv ships with pydantic-settings
        return
    for key, value in dotenv_values(env_file).items():
        if value is not None and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
results: list[tuple[str, str, str]] = []


def report(status: str, label: str, detail: str = "") -> None:
    results.append((status, label, detail))
    line = f"[{status}] {label}"
    if detail:
        line += f" — {detail}"
    print(line, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Lexicon setup smoke test")
    parser.add_argument(
        "--skip-bedrock-call",
        action="store_true",
        help="Do not make a live embedding request (avoids a (tiny) Bedrock charge).",
    )
    args = parser.parse_args()

    from lexicon.config import get_settings

    settings = get_settings()
    print("=" * 68)
    print("Lexicon smoke test")
    print("=" * 68)
    print(f"region={settings.aws_region}  vector_bucket={settings.vector_bucket}  "
          f"vector_index={settings.vector_index}  table={settings.dynamodb_table}")
    print("-" * 68)

    import boto3
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

    try:
        session = boto3.Session(region_name=settings.aws_region)
        identity = session.client("sts").get_caller_identity()
    except (NoCredentialsError, BotoCoreError, ClientError) as error:
        report(FAIL, "AWS credentials", f"{type(error).__name__}: {error}")
        report(SKIP, "All AWS checks", "no usable credentials — set AWS_PROFILE and retry")
        return _finish()

    report(
        PASS,
        "AWS identity",
        f"account={identity['Account']} arn={identity['Arn']}",
    )

    if settings.aws_region != "us-east-1":
        report(WARN, "Region", f"expected us-east-1, got {settings.aws_region}")

    _check_bedrock(session, settings, args.skip_bedrock_call)
    _check_vectors(session, settings)
    _check_dynamodb(session, settings)
    _check_cognito(session, settings)
    _check_api(settings)
    _check_seeded(session, settings)

    return _finish()


def _check_bedrock(session, settings, skip_call: bool) -> None:
    if skip_call:
        report(SKIP, "Bedrock embedding call", "skipped via --skip-bedrock-call")
        return
    from botocore.exceptions import ClientError

    from lexicon.bedrock import BedrockClient

    try:
        vector = BedrockClient().embed("lexicon connectivity check")
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code == "AccessDeniedException":
            report(
                FAIL,
                "Bedrock model access",
                "AccessDenied — enable Titan V2 + Nova Lite in the Bedrock console (§1)",
            )
        else:
            report(FAIL, "Bedrock embedding call", f"{code}: {error}")
        return
    except Exception as error:  # noqa: BLE001 - surface anything unexpected
        report(FAIL, "Bedrock embedding call", f"{type(error).__name__}: {error}")
        return

    if len(vector) == settings.embedding_dimensions:
        report(PASS, "Bedrock embedding call", f"{settings.embed_model_id} -> {len(vector)} dims")
    else:
        report(
            FAIL,
            "Embedding dimension mismatch",
            f"expected {settings.embedding_dimensions}, got {len(vector)}",
        )


def _check_vectors(session, settings) -> None:
    from botocore.exceptions import ClientError

    client = session.client("s3vectors")
    try:
        buckets = {
            item["vectorBucketName"]
            for item in client.list_vector_buckets().get("vectorBuckets", [])
        }
    except ClientError as error:
        report(FAIL, "S3 Vectors reachable", str(error))
        return

    if settings.vector_bucket not in buckets:
        report(
            FAIL,
            "S3 Vectors bucket",
            f"{settings.vector_bucket!r} not found — run infra/bootstrap_s3vectors.py",
        )
        return
    report(PASS, "S3 Vectors bucket", settings.vector_bucket)

    try:
        client.get_index(
            vectorBucketName=settings.vector_bucket,
            indexName=settings.vector_index,
        )
    except ClientError as error:
        report(FAIL, "S3 Vectors index", f"{settings.vector_index!r} missing — {error}")
        return
    report(PASS, "S3 Vectors index", settings.vector_index)


def _check_dynamodb(session, settings) -> None:
    from botocore.exceptions import ClientError

    try:
        table = session.client("dynamodb").describe_table(TableName=settings.dynamodb_table)
    except ClientError as error:
        report(
            FAIL,
            "DynamoDB table",
            f"{settings.dynamodb_table!r} missing — deploy the CDK stack — {error}",
        )
        return
    report(PASS, "DynamoDB table", f"{settings.dynamodb_table} ({table['Table']['TableStatus']})")


def _check_cognito(session, settings) -> None:
    if not settings.cognito_user_pool_id:
        report(SKIP, "Cognito", "LEXICON_COGNITO_USER_POOL_ID not set (fine for local dev)")
        return
    from botocore.exceptions import ClientError

    try:
        pool = session.client("cognito-idp").describe_user_pool(
            UserPoolId=settings.cognito_user_pool_id
        )
    except ClientError as error:
        report(FAIL, "Cognito user pool", str(error))
        return
    name = pool["UserPool"].get("Name", "?")
    report(PASS, "Cognito user pool", f"{name} ({settings.cognito_user_pool_id})")


def _check_api(settings) -> None:
    del settings  # the API URL is a deployment output, not a settings field
    base = os.environ.get("LEXICON_API_BASE")
    if not base:
        report(SKIP, "API health", "LEXICON_API_BASE not set (set it to the Function URL)")
        return
    url = base.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        report(FAIL, "API health", f"{url} unreachable — {error}")
        return
    if payload.get("status") == "ok":
        report(PASS, "API health", url)
    else:
        report(WARN, "API health", f"unexpected payload: {payload}")


def _check_seeded(session, settings) -> None:
    from botocore.exceptions import ClientError

    try:
        response = session.client("s3vectors").list_vectors(
            vectorBucketName=settings.vector_bucket,
            indexName=settings.vector_index,
            maxResults=5,
        )
    except ClientError as error:
        report(WARN, "Corpus seeded", f"could not list vectors — {error}")
        return
    count = len(response.get("vectors", []))
    if count:
        report(PASS, "Corpus seeded", f"at least {count} vector(s) present")
    else:
        report(WARN, "Corpus seeded", "index is empty — run sync-sources then refill")


def _finish() -> int:
    print("-" * 68)
    failures = [item for item in results if item[0] == FAIL]
    warnings = [item for item in results if item[0] == WARN]
    passes = [item for item in results if item[0] == PASS]
    print(f"summary: {len(passes)} passed, {len(warnings)} warning(s), {len(failures)} failed")
    if failures:
        print("blocking failures:")
        for _, label, detail in failures:
            print(f"  - {label}: {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
