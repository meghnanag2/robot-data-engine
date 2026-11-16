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
    assert dispatch(api, "POST", "/runs", None)[0] == 405
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
