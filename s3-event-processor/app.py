#!/usr/bin/env python3
import aws_cdk as cdk

from s3_event_processor.stack import S3EventProcessorStack

app = cdk.App()

# Environment-agnostic: deploys to whatever account/region your CLI is using.
# Pin with env=cdk.Environment(account="123456789012", region="us-east-1") if needed.
S3EventProcessorStack(app, "S3EventProcessorStack")

app.synth()
