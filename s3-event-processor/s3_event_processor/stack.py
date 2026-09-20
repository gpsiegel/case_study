from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
)
from constructs import Construct

LAMBDA_SRC = Path(__file__).resolve().parent.parent / "lambda_src"


class S3EventProcessorStack(Stack):
    """S3 bucket -> (ObjectCreated event) -> Python Lambda that parses the file."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ------------------------------------------------------------------ #
        # Bucket
        # ------------------------------------------------------------------ #
        bucket = s3.Bucket(
            self,
            "IngestBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            # Demo-friendly: `cdk destroy` removes the bucket and its objects.
            # For production use RemovalPolicy.RETAIN and drop auto_delete_objects.
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ------------------------------------------------------------------ #
        # Lambda (Python, latest GA runtime) with an explicit least-privilege role
        # ------------------------------------------------------------------ #
        function_role = iam.Role(
            self,
            "ProcessorRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Execution role for the S3 single-line file processor",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        log_group = logs.LogGroup(
            self,
            "ProcessorLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        processor = _lambda.Function(
            self,
            "Processor",
            runtime=_lambda.Runtime.PYTHON_3_14,
            architecture=_lambda.Architecture.ARM_64,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset(str(LAMBDA_SRC)),
            role=function_role,
            timeout=Duration.seconds(30),
            memory_size=256,
            log_group=log_group,
            # Failed async invocations (after retries) land in an SQS DLQ.
            dead_letter_queue_enabled=True,
            retry_attempts=2,
            environment={"LOG_LEVEL": "INFO", "MAX_OBJECT_BYTES": str(1024 * 1024)},
        )

        # ------------------------------------------------------------------ #
        # Bucket policy
        # ------------------------------------------------------------------ #
        # (1) Reject any request that is not sent over TLS.
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="DenyInsecureTransport",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["s3:*"],
                resources=[bucket.bucket_arn, bucket.arn_for_objects("*")],
                conditions={"Bool": {"aws:SecureTransport": "false"}},
            )
        )

        # (2) Let only the processor's role read objects (read-only, objects only).
        bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowProcessorRead",
                effect=iam.Effect.ALLOW,
                principals=[function_role],
                actions=["s3:GetObject"],
                resources=[bucket.arn_for_objects("*")],
            )
        )

        # ------------------------------------------------------------------ #
        # Event notification: any new object -> Lambda
        # ------------------------------------------------------------------ #
        bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(processor),
        )

        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "FunctionName", value=processor.function_name)
