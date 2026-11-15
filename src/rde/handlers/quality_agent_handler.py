"""Lambda entry point for the AI quality agent (last step of the pipeline)."""

from __future__ import annotations

import os

import boto3

from rde.config import get_settings
from rde.services.quality_agent import AthenaStats, QualityAgent
from rde.storage.s3 import ObjectStore


def handler(event: dict, context: object) -> dict:
    s = get_settings()
    agent = QualityAgent(
        AthenaStats(boto3.client("athena"), os.environ["ATHENA_WORKGROUP"], os.environ["GLUE_DATABASE"]),
        boto3.client("bedrock-runtime"),
        os.environ["BEDROCK_MODEL_ID"],
        float(os.environ.get("MAX_REJECT_RATE", "0.2")),
    )
    dataset, run_id = event["dataset"], event["run_id"]
    report = agent.review(dataset, run_id)

    key = f"reports/quality/dataset={dataset.replace('/', '__')}/run_id={run_id}.json"
    ObjectStore(s.data_bucket).put_json(key, report)

    topic = os.environ.get("ALERT_TOPIC_ARN")
    if topic:
        lines = [f"Verdict: {report['verdict'].upper()} ({report['source']})", report["summary"], ""]
        lines += [f"- {f}" for f in report["findings"]]
        lines += ["", f"Report: s3://{s.data_bucket}/{key}"]
        boto3.client("sns").publish(
            TopicArn=topic,
            Subject=f"[rde-{s.env}] data quality {report['verdict'].upper()}: {run_id}"[:100],
            Message="\n".join(lines),
        )
    return {"verdict": report["verdict"], "report_key": key, "summary": report["summary"]}
