import io
import json
from datetime import UTC, datetime

import boto3
from moto import mock_aws

from rde.services.catalog_api import CatalogApi, dispatch

ARN = "arn:aws:states:us-west-2:123456789012:stateMachine:rde-dev-pipeline"


class FakeSfn:
    def list_executions(self, **kwargs):
        start = datetime(2026, 11, 15, 21, 50, 18, 66000, tzinfo=UTC)
        return {"executions": [{"status": "SUCCEEDED", "startDate": start, "stopDate": start}]}


class FakeAthena:
    def __init__(self):
        self.queries = []

    def query(self, sql):
        self.queries.append(sql)
        if "video_frames" in sql:
            return [
                {
                    "frame_s3": "s3://test-bucket/processed/frames/x/t_000000000.jpg",
                    "episode_index": "0",
                    "timestamp_s": "0.0",
                    "brightness": "240",
                    "contrast": "40",
                    "blur_score": "900",
                }
            ]
        return [{"status": "clean", "episodes": "206"}]


def _api(s3):
    return CatalogApi(
        s3=s3, sfn=FakeSfn(), athena_stats=FakeAthena(), bucket="test-bucket", state_machine_arn=ARN
    )


@mock_aws
def test_runs_join_executions_with_quality_reports():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket")
    s3.put_object(
        Bucket="test-bucket",
        Key="reports/quality/dataset=lerobot__pusht/run_id=20261115T215018066Z.json",
        Body=json.dumps({"verdict": "ok"}).encode(),
    )
    status, body = dispatch(_api(s3), "GET", "/runs", None)
    assert status == 200
    assert body["runs"][0] == {
        "run_id": "20261115T215018066Z",
        "status": "SUCCEEDED",
        "started_at": "2026-11-15T21:50:18.066000+00:00",
        "stopped_at": "2026-11-15T21:50:18.066000+00:00",
        "verdict": "ok",
        "has_report": True,
    }


@mock_aws
def test_report_404_and_bad_input_400():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket")
    api = _api(s3)
    assert dispatch(api, "GET", "/runs/20261115T215018066Z", None)[0] == 404
    assert dispatch(api, "GET", "/runs/1';DROP TABLE x--", None)[0] == 400
    assert dispatch(api, "DELETE", "/runs", None)[0] == 405
    assert dispatch(api, "GET", "/nope", None)[0] == 404


@mock_aws
def test_frames_are_presigned_and_limit_is_bounded():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket")
    api = _api(s3)
    status, body = dispatch(api, "GET", "/runs/r1/frames", {"limit": "9999"})
    assert status == 200
    assert "Signature" in body["frames"][0]["url"] and "frame_s3" not in body["frames"][0]
    assert "LIMIT 48" in api.athena_stats.queries[-1]


def test_episodes_cached():
    api = CatalogApi(s3=None, sfn=None, athena_stats=FakeAthena(), bucket="b", state_machine_arn=ARN)
    dispatch(api, "GET", "/runs/r1/episodes", None)
    dispatch(api, "GET", "/runs/r1/episodes", None)
    assert len(api.athena_stats.queries) == 2  # summary + sample once, then served from cache


def test_handler_shape(monkeypatch):
    from rde.handlers import api_handler

    class Stub:
        def health(self):
            return {"status": "ok"}

    monkeypatch.setattr(api_handler, "_get_api", lambda: Stub())
    out = api_handler.handler({"rawPath": "/health", "requestContext": {"http": {"method": "GET"}}}, None)
    assert out["statusCode"] == 200 and json.loads(out["body"]) == {"status": "ok"}
    assert isinstance(io.StringIO(out["body"]).read(), str)


class RunSfn:
    def __init__(self, statuses):
        self.statuses, self.started = statuses, []

    def list_executions(self, **kwargs):
        now = datetime.now(UTC)
        return {
            "executions": [
                {"executionArn": f"arn:x:{i}", "status": s, "startDate": now, "stopDate": now}
                for i, s in enumerate(self.statuses)
            ]
        }

    def describe_execution(self, **kwargs):
        return {
            "output": json.dumps(
                {
                    "run_id": "r1",
                    "bytes_copied": 2_000_000,
                    "extract_results": [{"frames": 10}],
                    "clean": {"glue_run_id": "jr_1"},
                }
            )
        }

    def start_execution(self, **kwargs):
        self.started.append(kwargs)
        return {"executionArn": "arn:x:new", "startDate": datetime.now(UTC)}


class FakeGlue:
    def get_job_run(self, **kwargs):
        return {"JobRun": {"DPUSeconds": 3600}}


def _run_api(statuses, key="secret", max_runs=5):
    return CatalogApi(
        s3=None,
        sfn=RunSfn(statuses),
        athena_stats=FakeAthena(),
        bucket="b",
        state_machine_arn=ARN,
        admin_key_loader=lambda: key,
        max_runs_per_day=max_runs,
    )


def test_start_run_requires_admin_key_and_guards_cost():
    api = _run_api([])
    assert dispatch(api, "POST", "/runs", None, {"X-Admin-Key": "wrong"})[0] == 403
    assert dispatch(api, "POST", "/runs", None, {})[0] == 403
    assert dispatch(_run_api(["RUNNING"]), "POST", "/runs", None, {"x-admin-key": "secret"})[0] == 409
    assert dispatch(_run_api(["SUCCEEDED"] * 5), "POST", "/runs", None, {"x-admin-key": "secret"})[0] == 429
    status, body = dispatch(api, "POST", "/runs", None, {"x-admin-key": "secret"})
    assert status == 202 and body["started"] and len(api.sfn.started) == 1


def test_no_key_configured_means_nobody_can_start():
    assert dispatch(_run_api([], key=""), "POST", "/runs", None, {"x-admin-key": ""})[0] == 403


def test_metrics_aggregate_runs_and_estimate_cost():
    api = _run_api(["SUCCEEDED", "FAILED"])
    api.glue, api.s3 = FakeGlue(), None
    api._report_index = lambda: {
        "r1": {"verdict": "ok", "stats": {"clean_frames": 100, "quarantine_by_reason": {"a": 3}}}
    }
    status, body = dispatch(api, "GET", "/metrics", None)
    s = body["summary"]
    assert status == 200 and s["runs"] == 2 and s["success_rate"] == 0.5
    assert s["mb_ingested"] == 4.0 and s["frames_extracted"] == 20 and s["est_glue_cost_usd"] == 0.88
    assert body["runs"][0]["verdict"] == "ok"
