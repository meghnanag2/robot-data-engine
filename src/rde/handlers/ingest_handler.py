"""Lambda entry point. Parses the event, wires dependencies, calls the service. No logic here."""

from __future__ import annotations

import re
import time

from rde.config import get_settings
from rde.services.ingest import IngestService
from rde.sources.huggingface import HuggingFaceDatasets
from rde.storage.s3 import ObjectStore


def handler(event: dict, context: object) -> dict:
    s = get_settings()
    service = IngestService(
        ObjectStore(s.data_bucket),
        HuggingFaceDatasets(s.hf_base_url, s.hf_revision, s.http_timeout_s),
    )
    result = service.run(
        dataset=event.get("dataset", "lerobot/pusht"),
        # run ids sort by time: "2026-11-15T21:00:00.123Z" -> "20261115T210000123Z"
        run_id=re.sub(
            r"[^0-9A-Za-z]", "", event.get("run_id") or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        ),
        max_videos_per_camera=int(event.get("max_videos", 2)),
    )
    return result.to_dict()
