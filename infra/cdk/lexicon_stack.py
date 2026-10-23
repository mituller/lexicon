"""Lexicon AWS infrastructure (us-east-1).

Provisions:
* Cognito User Pool + app client + Hosted UI domain, with optional Google/GitHub IdPs.
* DynamoDB single table (PK/SK) for progress, aggregates, and user lists.
* The Python FastAPI runtime as a Lambda function behind a Function URL, with least-privilege
  access to DynamoDB, Bedrock, and S3 Vectors.

S3 Vectors (bucket + index) is created by ``infra/bootstrap_s3vectors.py`` because the index
dimension/metric/non-filterable keys are immutable once created.

Google/GitHub IdPs are optional and enabled via CDK context so the stack deploys before the
OAuth apps exist, then re-deploys once they do:

    cdk deploy \
      -c google_client_id=123.apps.googleusercontent.com \
      -c google_secret_name=lexicon/google-oauth \
      -c github_client_id=Iv1.abc \
      -c github_secret_name=lexicon/github-oauth
"""

from __future__ import annotations

import pathlib

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    DockerImage,
    Duration,
    RemovalPolicy,
    SecretValue,
    Stack,
)
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PYTHON_DIR = REPO_ROOT / "python"

# Keep these in sync with infra/bootstrap_s3vectors.py and python/lexicon/config.py defaults.
VECTOR_BUCKET = "lexicon-vectors"
VECTOR_INDEX = "vocabulary"

# Local dev loopback URLs. The desktop shell uses the fixed loopback port; the web dev server
# uses 5173. GitHub Pages / custom-domain URLs get added when the web ships.
DESKTOP_CALLBACK_URL = "http://127.0.0.1:4200/callback"
DEV_WEB_URL = "http://localhost:5173"
# Both hostnames for the dev server: Vite may be opened as either, and a redirect_uri that
# doesn't exactly match the allow-list fails with Cognito's opaque "redirect_mismatch".
DEV_WEB_URL_ALT = "http://127.0.0.1:5173"
DEFAULT_CALLBACK_URLS = [DESKTOP_CALLBACK_URL, DEV_WEB_URL, DEV_WEB_URL_ALT]
DEFAULT_LOGOUT_URLS = [DEV_WEB_URL, DEV_WEB_URL_ALT]


class LexiconStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # `cdk deploy -c dev=true` destroys data resources on stack delete / rollback. That
        # avoids the "resource already exists" collisions we hit when a failed deploy rolled
        # back and left RETAIN-ed resources behind. Production omits the flag and keeps RETAIN.
        dev_mode = bool(self.node.try_get_context("dev"))
        self.data_removal_policy = RemovalPolicy.DESTROY if dev_mode else RemovalPolicy.RETAIN

        table = self._create_table()
        user_pool, user_pool_client, hosted_domain = self._create_cognito()
        function = self._create_api(table, user_pool, user_pool_client, hosted_domain)

        function_url = function.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)
        CfnOutput(self, "ApiUrl", value=function_url.url)
        CfnOutput(self, "DynamoTableName", value=table.table_name)
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=user_pool_client.user_pool_client_id)
        CfnOutput(self, "CognitoHostedUiDomain", value=hosted_domain)
        CfnOutput(self, "CognitoIssuer", value=user_pool.user_pool_provider_url)
        CfnOutput(self, "VectorBucket", value=VECTOR_BUCKET)
        CfnOutput(self, "VectorIndex", value=VECTOR_INDEX)

    # --- DynamoDB ---

    def _create_table(self) -> dynamodb.Table:
        return dynamodb.Table(
            self,
            "LexiconTable",
            table_name="lexicon",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            # Study sessions (SK SESSION#...) carry a ttl so abandoned ones expire.
            time_to_live_attribute="ttl",
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=self.data_removal_policy,
        )

    # --- Cognito ---

    def _create_cognito(self) -> tuple[cognito.UserPool, cognito.UserPoolClient, str]:
        user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name="lexicon",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True)
            ),
            removal_policy=self.data_removal_policy,
        )

        # Hosted UI domain (free, AWS-managed TLS - no custom domain needed).
        domain_prefix = f"lexicon-{self.account}"
        user_pool.add_domain(
            "CognitoDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
        )
        hosted_domain = f"{domain_prefix}.auth.{self.region}.amazoncognito.com"

        providers = self._create_identity_providers(user_pool)

        user_pool_client = user_pool.add_client(
            "AppClient",
            user_pool_client_name="lexicon-app",
            generate_secret=False,  # public client (PKCE) for desktop + web
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=self._callback_urls(),
                logout_urls=self._logout_urls(),
            ),
            supported_identity_providers=providers,
        )
        return user_pool, user_pool_client, hosted_domain

    def _create_identity_providers(
        self, user_pool: cognito.UserPool
    ) -> list[cognito.UserPoolClientIdentityProvider]:
        """Wire Google/GitHub when their client ids are supplied via CDK context."""
        providers = [cognito.UserPoolClientIdentityProvider.COGNITO]

        google_client_id = self.node.try_get_context("google_client_id")
        if google_client_id:
            cognito.UserPoolIdentityProviderGoogle(
                self,
                "GoogleIdP",
                user_pool=user_pool,
                client_id=google_client_id,
                client_secret_value=SecretValue.secrets_manager(
                    self.node.try_get_context("google_secret_name") or "lexicon/google-oauth"
                ),
                scopes=["openid", "email", "profile"],
                attribute_mapping=cognito.AttributeMapping(
                    email=cognito.ProviderAttribute.GOOGLE_EMAIL,
                ),
            )
            providers.append(cognito.UserPoolClientIdentityProvider.GOOGLE)

        github_client_id = self.node.try_get_context("github_client_id")
        if github_client_id:
            cognito.UserPoolIdentityProviderGithub(
                self,
                "GitHubIdP",
                user_pool=user_pool,
                client_id=github_client_id,
                client_secret_value=SecretValue.secrets_manager(
                    self.node.try_get_context("github_secret_name") or "lexicon/github-oauth"
                ),
                scopes=["user:email"],
            )
            providers.append(cognito.UserPoolClientIdentityProvider.GITHUB)

        return providers

    def _callback_urls(self) -> list[str]:
        extra = list(self.node.try_get_context("extra_callback_urls") or [])
        return DEFAULT_CALLBACK_URLS + extra

    def _logout_urls(self) -> list[str]:
        extra = list(self.node.try_get_context("extra_logout_urls") or [])
        return DEFAULT_LOGOUT_URLS + extra

    # --- Lambda (Python runtime) ---

    def _create_api(
        self,
        table: dynamodb.Table,
        user_pool: cognito.UserPool,
        user_pool_client: cognito.UserPoolClient,
        hosted_domain: str,
    ) -> lambda_.Function:
        # Explicit log group instead of the deprecated `log_retention` (which spins up a
        # custom-resource Lambda). Name matches the function's default log group.
        log_group = logs.LogGroup(
            self,
            "ApiLogGroup",
            log_group_name="/aws/lambda/lexicon-api",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        function = lambda_.Function(
            self,
            "ApiFunction",
            function_name="lexicon-api",
            runtime=lambda_.Runtime.PYTHON_3_12,
            # ARM64: cheaper/faster, and matches the arch-specific build image below so the
            # bundled wheels are always the right ones.
            architecture=lambda_.Architecture.ARM_64,
            handler="lexicon.api.handler",
            memory_size=1024,
            timeout=Duration.seconds(60),
            log_group=log_group,
            code=lambda_.Code.from_asset(
                str(PYTHON_DIR),
                # Keep the asset small: never ship the local venv, tests, or caches.
                exclude=[
                    ".venv",
                    "**/__pycache__",
                    "**/*.pyc",
                    "tests",
                    ".pytest_cache",
                    ".mypy_cache",
                    ".ruff_cache",
                    "*.egg-info",
                    ".env",
                ],
                bundling=BundlingOptions(
                    # Pinned arch-specific SAM build image: deterministic regardless of host CPU.
                    image=DockerImage.from_registry(
                        "public.ecr.aws/sam/build-python3.12:latest-arm64"
                    ),
                    # Copy ONLY the package (never `.`), so the local venv/tests/caches can
                    # never end up in the Lambda artifact and blow the 250 MB unzipped limit.
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements.txt -t /asset-output "
                        "&& cp -a lexicon /asset-output",
                    ],
                ),
            ),
            environment={
                "LEXICON_AWS_REGION": self.region,
                "LEXICON_DYNAMODB_TABLE": table.table_name,
                "LEXICON_VECTOR_BUCKET": VECTOR_BUCKET,
                "LEXICON_VECTOR_INDEX": VECTOR_INDEX,
                "LEXICON_COGNITO_USER_POOL_ID": user_pool.user_pool_id,
                "LEXICON_COGNITO_CLIENT_ID": user_pool_client.user_pool_client_id,
                "LEXICON_COGNITO_DOMAIN": hosted_domain,
                "LEXICON_COGNITO_CALLBACK_URL": DESKTOP_CALLBACK_URL,
            },
        )

        table.grant_read_write_data(function)

        # Bedrock: scoped to foundation models + cross-region inference profiles, not "*".
        function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*",
                ],
            )
        )

        # S3 Vectors: scoped to this bucket and its indexes.
        vector_bucket_arn = (
            f"arn:aws:s3vectors:{self.region}:{self.account}:bucket/{VECTOR_BUCKET}"
        )
        function.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "s3vectors:QueryVectors",
                    "s3vectors:GetVectors",
                    "s3vectors:ListVectors",
                    "s3vectors:PutVectors",
                    "s3vectors:DeleteVectors",
                ],
                resources=[vector_bucket_arn, f"{vector_bucket_arn}/index/*"],
            )
        )
        return function
