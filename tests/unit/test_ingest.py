import io
from contextlib import contextmanager

import boto3
from moto import mock_aws

from rde.services.ingest import IngestService, plan_files
from rde.sources.huggingface import RemoteFile
from rde.storage.s3 import ObjectStore

FILES = [
    RemoteFile("meta/info.json", 4),
    RemoteFile("data/chunk-000/file-000.parquet", 4),
    RemoteFile("videos/cam/chunk-000/file-000.mp4", 4),
    RemoteFile("videos/cam/chunk-000/file-001.mp4", 4),
    RemoteFile("README.md", 4),
]


class FakeHub:
    def list_files(self, repo_id):
        return FILES

    @contextmanager
    def open(self, repo_id, path):
        yield io.BytesIO(b"data")


def test_plan_keeps_meta_data_and_n_videos():
    planned = [f.path for f in plan_files(FILES, max_videos_per_camera=1)]
    assert planned == [
        "data/chunk-000/file-000.parquet",
        "meta/info.json",
        "videos/cam/chunk-000/file-000.mp4",
    ]


@mock_aws
def test_ingest_is_idempotent():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket")
    service = IngestService(ObjectStore("test-bucket", s3), FakeHub())

    first = service.run("lerobot/pusht", "r1", max_videos_per_camera=2)
    second = service.run("lerobot/pusht", "r2", max_videos_per_camera=2)

    assert first.files_copied == 4 and first.files_skipped == 0
    assert second.files_copied == 0 and second.files_skipped == 4
    assert first.video_keys == second.video_keys
    assert first.video_keys[0] == "raw/lerobot/lerobot__pusht/videos/cam/chunk-000/file-000.mp4"
