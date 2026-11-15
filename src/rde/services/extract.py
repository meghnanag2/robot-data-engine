"""Extract service: sample frames from one MP4, store JPEGs + per-frame quality metrics.

A video that fails to decode is recorded under processed/extract_errors/ and reported,
so one bad file does not fail the whole pipeline run.
"""

from __future__ import annotations

import io
import os
import tempfile
import traceback
from dataclasses import asdict, dataclass

from rde.observability import get_logger, log
from rde.quality.frame_metrics import frame_metrics
from rde.storage import paths
from rde.storage.paths import VideoRef
from rde.storage.s3 import ObjectStore

logger = get_logger(__name__)


@dataclass
class ExtractResult:
    video_key: str
    status: str
    frames: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def should_sample(t_s: float, next_t_s: float) -> bool:
    return t_s + 1e-6 >= next_t_s


class ExtractService:
    def __init__(self, store: ObjectStore, sample_every_s: float = 1.0) -> None:
        self.store = store
        self.sample_every_s = sample_every_s

    def run(self, dataset: str, run_id: str, video_key: str) -> ExtractResult:
        video = VideoRef.from_key(video_key)
        fd, local = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        try:
            self.store.download(video_key, local)
            rows = self._decode(local, dataset, run_id, video_key, video)
            self.store.put_jsonl(paths.frame_meta_key(dataset, run_id, video), rows)
            result = ExtractResult(video_key, "ok", frames=len(rows))
        except Exception as exc:
            self.store.put_json(
                paths.extract_error_key(dataset, run_id, video),
                {"video_key": video_key, "error": repr(exc), "trace": traceback.format_exc()},
            )
            result = ExtractResult(video_key, "error", error=repr(exc))
        finally:
            os.remove(local)
        log(logger, "extract complete", run_id=run_id, **result.to_dict())
        return result

    def _decode(self, local: str, dataset: str, run_id: str, video_key: str, video: VideoRef) -> list[dict]:
        import av  # from the Lambda layer; imported here so the package loads without it

        rows: list[dict] = []
        with av.open(local) as container:
            stream = container.streams.video[0]
            codec = stream.codec_context.name
            fps = float(stream.average_rate or 0)
            next_t = 0.0
            for frame in container.decode(stream):
                if frame.pts is None:
                    continue
                t = float(frame.pts * stream.time_base)
                if not should_sample(t, next_t):
                    continue
                next_t = t + self.sample_every_s
                buf = io.BytesIO()
                frame.to_image().save(buf, format="JPEG", quality=90)
                key = paths.frame_key(dataset, video, t)
                self.store.put_bytes(key, buf.getvalue(), "image/jpeg")
                rows.append(
                    {
                        "dataset": dataset,
                        "run_id": run_id,
                        "source_video": video_key,
                        "camera": video.camera,
                        "chunk_index": video.chunk_index,
                        "file_index": video.file_index,
                        "timestamp_s": round(t, 4),
                        "frame_s3": self.store.uri(key),
                        "codec": codec,
                        "video_fps": fps,
                        **frame_metrics(frame.to_ndarray(format="rgb24")),
                    }
                )
        return rows
