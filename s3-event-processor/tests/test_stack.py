"""Stack assertions. Requires `pip install -r requirements-dev.txt`."""

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from s3_event_processor.stack import S3EventProcessorStack


def _template() -> Template:
    return Template.from_stack(S3EventProcessorStack(cdk.App(), "TestStack"))


def test_bucket_is_private_encrypted_and_versioned():
    _template().has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "VersioningConfiguration": {"Status": "Enabled"},
            "BucketEncryption": Match.any_value(),
        },
    )


def test_bucket_policy_denies_insecure_transport_and_allows_processor_read():
    t = _template()
    t.has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Sid": "DenyInsecureTransport",
                                "Effect": "Deny",
                                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                            }
                        ),
                        Match.object_like(
                            {"Sid": "AllowProcessorRead", "Effect": "Allow", "Action": "s3:GetObject"}
                        ),
                    ]
                )
            }
        },
    )


def test_lambda_uses_python_3_14():
    _template().has_resource_properties(
        "AWS::Lambda::Function",
        {"Runtime": "python3.14", "Handler": "handler.lambda_handler"},
    )


def test_bucket_notifies_lambda_on_object_created():
    t = _template()
    t.resource_count_is("Custom::S3BucketNotifications", 1)
    t.has_resource_properties(
        "Custom::S3BucketNotifications",
        {
            "NotificationConfiguration": {
                "LambdaFunctionConfigurations": [
                    Match.object_like({"Events": ["s3:ObjectCreated:*"]})
                ]
            }
        },
    )
    t.has_resource_properties(
        "AWS::Lambda::Permission",
        {"Action": "lambda:InvokeFunction", "Principal": "s3.amazonaws.com"},
    )
