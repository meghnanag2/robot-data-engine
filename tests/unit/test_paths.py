import pytest

from rde.storage import paths
from rde.storage.paths import VideoRef


def test_video_ref_parses_lerobot_v3_key():
    v = VideoRef.from_key("raw/lerobot/lerobot__pusht/videos/observation.image/chunk-000/file-002.mp4")
    assert v == VideoRef("observation.image", 0, 2)
    assert v.stem == "observation.image/chunk-000/file-002"


def test_video_ref_rejects_other_paths():
    with pytest.raises(ValueError):
        VideoRef.from_key("raw/lerobot/x/data/chunk-000/file-000.parquet")


def test_keys_are_partitioned_by_dataset_and_run():
    v = VideoRef("cam", 1, 3)
    assert paths.frame_key("lerobot/pusht", v, 1.5) == (
        "processed/frames/dataset=lerobot__pusht/cam/chunk-001/file-003/t_000001500.jpg"
    )
    assert paths.frame_meta_key("lerobot/pusht", "r1", v) == (
        "processed/frame_meta/dataset=lerobot__pusht/run_id=r1/cam_chunk-001_file-003.jsonl"
    )
