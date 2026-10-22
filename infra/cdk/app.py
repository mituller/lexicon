"""CDK entry point.

Deploys the AWS side of Lexicon to us-east-1: Cognito, DynamoDB, and the Python FastAPI Lambda
behind a Function URL. S3 Vectors is provisioned separately (see infra/bootstrap_s3vectors.py)
because the index configuration is fixed at creation.

Usage:
    cd infra/cdk
    pip install -r requirements.txt
    cdk deploy
"""

from __future__ import annotations

import aws_cdk as cdk

from lexicon_stack import LexiconStack

app = cdk.App()
LexiconStack(
    app,
    "LexiconStack",
    env=cdk.Environment(region="us-east-1"),
    description="Lexicon: Cognito + DynamoDB + Python RAG API (us-east-1)",
)
app.synth()
