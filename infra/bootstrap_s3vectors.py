"""Idempotently create the S3 Vectors bucket + index for Lexicon.

Kept out of CDK because the index configuration (dimension, distance metric, and the set of
non-filterable metadata keys) is **immutable once the index is created**.

Usage:
    python infra/bootstrap_s3vectors.py

Reads AWS credentials from the standard chain; region is fixed to us-east-1.
"""

from __future__ import annotations

import sys

import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
VECTOR_BUCKET = "lexicon-vectors"
VECTOR_INDEX = "vocabulary"
DIMENSION = 1024  # Titan Text Embeddings V2 default
DISTANCE_METRIC = "cosine"

# Large text returned with results but not used for filtering. Fixed at index creation.
NON_FILTERABLE_METADATA_KEYS = ["term", "definition", "example", "synonyms", "pos", "source"]


def main() -> int:
    client = boto3.client("s3vectors", region_name=REGION)

    _ignore_conflict(
        lambda: client.create_vector_bucket(vectorBucketName=VECTOR_BUCKET),
        "vector bucket",
        VECTOR_BUCKET,
    )

    _ignore_conflict(
        lambda: client.create_index(
            vectorBucketName=VECTOR_BUCKET,
            indexName=VECTOR_INDEX,
            dataType="float32",
            dimension=DIMENSION,
            distanceMetric=DISTANCE_METRIC,
            metadataConfiguration={"nonFilterableMetadataKeys": NON_FILTERABLE_METADATA_KEYS},
        ),
        "vector index",
        VECTOR_INDEX,
    )

    print(f"S3 Vectors ready: bucket={VECTOR_BUCKET} index={VECTOR_INDEX} dim={DIMENSION}")
    return 0


def _ignore_conflict(action, label: str, name: str) -> None:
    try:
        action()
        print(f"created {label} {name}")
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code in {"ConflictException", "BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
            print(f"{label} {name} already exists")
        else:
            raise


if __name__ == "__main__":
    raise SystemExit(main())
