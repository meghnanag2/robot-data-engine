"""The single source of truth for the S3 layout. Nothing else builds keys by hand.

raw/lerobot/<ds>/<hf path>                                  immutable copy of the source
processed/frames/dataset=<ds>/<camera>/chunk-NNN/file-NNN/t_<ms>.jpg
processed/frame_meta/dataset=<ds>/run_id=<run>/<video>.jsonl
processed/extract_errors/dataset=<ds>/run_id=<run>/<video>.json
warehouse/                                                  Iceberg tables
"""

from __future__ import annotations

import re
from dataclasses import dataclass

VIDEO_RE = re.compile(r"videos/(?P<camera>[^/]+)/chunk-(?P<chunk>\d+)/file-(?P<file>\d+)\.mp4$")


def dataset_slug(dataset: str) -> str:
    return dataset.replace("/", "__")


def raw_prefix(dataset: str) -> str:
    return f"raw/lerobot/{dataset_slug(dataset)}/"


def raw_key(dataset: str, hf_path: str) -> str:
    return raw_prefix(dataset) + hf_path


@dataclass(frozen=True)
class VideoRef:
    camera: str
    chunk_index: int
    file_index: int

    @classmethod
    def from_key(cls, key: str) -> VideoRef:
        m = VIDEO_RE.search(key)
        if not m:
            raise ValueError(f"not a LeRobot v3 video path: {key}")
        return cls(m["camera"], int(m["chunk"]), int(m["file"]))

    @property
    def stem(self) -> str:
        return f"{self.camera}/chunk-{self.chunk_index:03d}/file-{self.file_index:03d}"

    @property
    def flat(self) -> str:
        return self.stem.replace("/", "_")


def frame_key(dataset: str, video: VideoRef, t_s: float) -> str:
    ms = int(round(t_s * 1000))
    return f"processed/frames/dataset={dataset_slug(dataset)}/{video.stem}/t_{ms:09d}.jpg"


def frame_meta_prefix(dataset: str, run_id: str) -> str:
    return f"processed/frame_meta/dataset={dataset_slug(dataset)}/run_id={run_id}/"


def frame_meta_key(dataset: str, run_id: str, video: VideoRef) -> str:
    return frame_meta_prefix(dataset, run_id) + f"{video.flat}.jsonl"


def extract_error_key(dataset: str, run_id: str, video: VideoRef) -> str:
    return f"processed/extract_errors/dataset={dataset_slug(dataset)}/run_id={run_id}/{video.flat}.json"
