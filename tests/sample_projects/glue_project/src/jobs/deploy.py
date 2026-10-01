"""Deployment helper registering the nightly ETL job via boto3."""

import boto3

client = boto3.client("glue")

client.create_job(
    Name="nightly-etl",
    Role="arn:aws:iam::123456789012:role/GlueServiceRole",
    Command={
        "Name": "glueetl",
        "ScriptLocation": "s3://example-artifacts/jobs/etl_job.py",
    },
    DefaultArguments={
        "--job-language": "python",
        "--glue-version": "2.0",
    },
    GlueVersion="2.0",
)
