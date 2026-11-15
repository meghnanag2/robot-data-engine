"""Lambda entry point for one video. Called by the Step Functions Map state."""

from __future__ import annotations

from rde.config import get_settings
from rde.services.extract import ExtractService
from rde.storage.s3 import ObjectStore


def handler(event: dict, context: object) -> dict:
    s = get_settings()
    service = ExtractService(ObjectStore(event.get("bucket") or s.data_bucket), s.sample_every_s)
    return service.run(event["dataset"], event["run_id"], event["video_key"]).to_dict()
