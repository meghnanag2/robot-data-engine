"""Lambda entry point for API Gateway (HTTP API, payload v2). Read-only."""

from __future__ import annotations

import json
import os

import boto3

from rde.observability import get_logger, log
from rde.services.catalog_api import CatalogApi, dispatch
from rde.services.quality_agent import AthenaStats

logger = get_logger(__name__)
_api: CatalogApi | None = None  # reused across warm invocations (keeps the cache)


def _admin_key() -> str:
    """Admin key from SSM Parameter Store (SecureString), read only when someone tries to start a run."""
    name = os.environ.get("ADMIN_KEY_PARAM")
    if not name:
        return ""
    return boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def _get_api() -> CatalogApi:
    global _api
    if _api is None:
        _api = CatalogApi(
            s3=boto3.client("s3"),
            sfn=boto3.client("stepfunctions"),
            athena_stats=AthenaStats(
                boto3.client("athena"), os.environ["ATHENA_WORKGROUP"], os.environ["GLUE_DATABASE"]
            ),
            bucket=os.environ["DATA_BUCKET"],
            state_machine_arn=os.environ["STATE_MACHINE_ARN"],
            dataset=os.environ.get("DATASET", "lerobot/pusht"),
            glue=boto3.client("glue"),
            glue_job_name=os.environ.get("GLUE_JOB_NAME", ""),
            admin_key_loader=_admin_key,
            max_runs_per_day=int(os.environ.get("MAX_RUNS_PER_DAY", "5")),
        )
    return _api


def handler(event: dict, context: object) -> dict:
    http = event.get("requestContext", {}).get("http", {})
    method, path = http.get("method", "GET"), event.get("rawPath", "/")
    try:
        status, body = dispatch(
            _get_api(), method, path, event.get("queryStringParameters"), event.get("headers")
        )
    except Exception as exc:  # never leak internals to the browser
        log(logger, "api error", path=path, error=repr(exc))
        status, body = 500, {"error": "internal error"}
    log(logger, "api request", method=method, path=path, status=status)
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store" if method != "GET" else "max-age=30",
        },
        "body": json.dumps(body, default=str),
    }
