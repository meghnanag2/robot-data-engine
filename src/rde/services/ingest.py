"""Ingest service: copy a LeRobot v3 dataset from the Hugging Face Hub into S3 raw/.

Idempotent: files already in S3 with the same size are skipped, so reruns are cheap and safe.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from rde.observability import get_logger, log
from rde.sources.huggingface import HuggingFaceDatasets, RemoteFile
from rde.storage import paths
from rde.storage.s3 import ObjectStore

logger = get_logger(__name__)


@dataclass
class IngestResult:
    bucket: str
    dataset: str
    run_id: str
    raw_prefix: str
    files_copied: int = 0
    files_skipped: int = 0
    bytes_copied: int = 0
    video_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def plan_files(files: list[RemoteFile], max_videos_per_camera: int) -> list[RemoteFile]:
    """All metadata and parquet data, plus the first N video files per camera."""
    keep: list[RemoteFile] = []
    per_camera: dict[str, int] = {}
    for f in sorted(files, key=lambda x: x.path):
        if f.path.startswith("meta/") or (f.path.startswith("data/") and f.path.endswith(".parquet")):
            keep.append(f)
        elif f.path.startswith("videos/") and f.path.endswith(".mp4"):
            camera = f.path.split("/")[1]
            if per_camera.get(camera, 0) < max_videos_per_camera:
                per_camera[camera] = per_camera.get(camera, 0) + 1
                keep.append(f)
    return keep


class IngestService:
    def __init__(self, store: ObjectStore, hub: HuggingFaceDatasets) -> None:
        self.store = store
        self.hub = hub

    def run(self, dataset: str, run_id: str, max_videos_per_camera: int) -> IngestResult:
        result = IngestResult(self.store.bucket, dataset, run_id, paths.raw_prefix(dataset))
        for f in plan_files(self.hub.list_files(dataset), max_videos_per_camera):
            key = paths.raw_key(dataset, f.path)
            if self.store.size_of(key) == f.size:
                result.files_skipped += 1
            else:
                with self.hub.open(dataset, f.path) as stream:
                    self.store.upload_stream(stream, key)
                result.files_copied += 1
                result.bytes_copied += f.size
            if key.endswith(".mp4"):
                result.video_keys.append(key)
        log(
            logger,
            "ingest complete",
            **{k: v for k, v in result.to_dict().items() if k != "video_keys"},
            videos=len(result.video_keys),
        )
        return result
