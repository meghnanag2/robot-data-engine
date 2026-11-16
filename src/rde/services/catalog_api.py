"""Read-only catalog API behind API Gateway: runs, quality reports, episode stats, frame samples.

Every input is validated before it reaches a query, and nothing here can start a pipeline run or
change data. Athena results are cached in memory for a few minutes to keep the demo fast and cheap.
"""

from __future__ import annotations

import hmac
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from rde.observability import get_logger, log
from rde.storage import paths

logger = get_logger(__name__)

RUN_ID_RE = re.compile(r"^[0-9A-Za-z]{1,40}$")


class BadRequestError(ValueError):
    pass


class NotFoundError(LookupError):
    pass


class ForbiddenError(PermissionError):
    pass


class ConflictError(RuntimeError):
    pass


class TooManyRunsError(RuntimeError):
    pass


GLUE_USD_PER_DPU_HOUR = 0.44  # Glue Spark job price (us-west-2); used only for the cost estimate


def valid_run_id(run_id: str) -> str:
    if not RUN_ID_RE.match(run_id or ""):
        raise BadRequestError("run_id must be 1-40 letters or digits")
    return run_id


def bounded_int(raw: str | None, default: int, lo: int, hi: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except ValueError as exc:
        raise BadRequestError("limit must be an integer") from exc
    return max(lo, min(hi, value))


class TTLCache:
    def __init__(self, ttl_s: float = 300) -> None:
        self.ttl_s = ttl_s
        self._data: dict[str, tuple[float, Any]] = {}

    def get_or_set(self, key: str, fn: Callable[[], Any]) -> Any:
        hit = self._data.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl_s:
            return hit[1]
        value = fn()
        self._data[key] = (time.monotonic(), value)
        return value


@dataclass
class CatalogApi:
    s3: Any
    sfn: Any
    athena_stats: Any  # rde.services.quality_agent.AthenaStats (has .query)
    bucket: str
    state_machine_arn: str
    dataset: str = "lerobot/pusht"
    cache: TTLCache | None = None
    glue: Any = None
    admin_key_loader: Callable[[], str] | None = None  # reads the key from SSM, lazily
    max_runs_per_day: int = 5
    max_videos: int = 1
    glue_job_name: str = ""

    def __post_init__(self) -> None:
        self.cache = self.cache or TTLCache()

    # ---------- routes ----------

    def health(self) -> dict:
        return {"status": "ok"}

    def list_runs(self) -> dict:
        def _load() -> dict:
            execs = self.sfn.list_executions(stateMachineArn=self.state_machine_arn, maxResults=20)[
                "executions"
            ]
            reports = self._report_index()
            runs = []
            for e in execs:
                started = e["startDate"]
                run_id = started.strftime("%Y%m%dT%H%M%S") + f"{started.microsecond // 1000:03d}Z"
                rep = reports.get(run_id)
                runs.append(
                    {
                        "run_id": run_id,
                        "status": e["status"],
                        "started_at": started.isoformat(),
                        "stopped_at": e.get("stopDate").isoformat() if e.get("stopDate") else None,
                        "verdict": rep.get("verdict") if rep else None,
                        "has_report": rep is not None,
                    }
                )
            return {"dataset": self.dataset, "runs": runs}

        return self.cache.get_or_set("runs", _load)

    def run_report(self, run_id: str) -> dict:
        valid_run_id(run_id)
        key = self._report_key(run_id)
        try:
            body = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.s3.exceptions.NoSuchKey as exc:
            raise NotFoundError(f"no quality report for run {run_id}") from exc
        return json.loads(body)

    def run_episodes(self, run_id: str) -> dict:
        valid_run_id(run_id)
        ds = self.dataset

        def _load() -> dict:
            summary = self.athena_stats.query(f"""
                SELECT status, count(*) AS episodes, round(avg(num_frames), 1) AS avg_frames,
                       round(avg(duration_s), 2) AS avg_duration_s, sum(action_spikes) AS action_spikes
                FROM episodes WHERE dataset = '{ds}' AND run_id = '{run_id}'
                GROUP BY status ORDER BY status
            """)  # noqa: S608 - run_id validated, dataset from config
            sample = self.athena_stats.query(f"""
                SELECT episode_index, num_frames, round(duration_s, 2) AS duration_s, status, reasons
                FROM episodes WHERE dataset = '{ds}' AND run_id = '{run_id}'
                ORDER BY episode_index LIMIT 50
            """)  # noqa: S608
            return {"run_id": run_id, "summary": summary, "episodes": sample}

        return self.cache.get_or_set(f"episodes:{run_id}", _load)

    def run_frames(self, run_id: str, limit: int) -> dict:
        valid_run_id(run_id)
        ds = self.dataset

        def _load() -> list[dict]:
            return self.athena_stats.query(f"""
                SELECT frame_s3, episode_index, timestamp_s, brightness, contrast, blur_score
                FROM video_frames WHERE dataset = '{ds}' AND run_id = '{run_id}'
                ORDER BY timestamp_s LIMIT {int(limit)}
            """)  # noqa: S608

        rows = self.cache.get_or_set(f"frames:{run_id}:{limit}", _load)
        frames = []
        for r in rows:
            key = r["frame_s3"].split(f"s3://{self.bucket}/", 1)[-1]
            if not key.startswith("processed/frames/"):
                continue
            url = self.s3.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=3600
            )
            frames.append({**{k: v for k, v in r.items() if k != "frame_s3"}, "url": url})
        return {"run_id": run_id, "frames": frames}

    def metrics(self) -> dict:
        """Aggregate metrics across recent runs, from Step Functions outputs and Glue run stats."""

        def _load() -> dict:
            execs = self.sfn.list_executions(stateMachineArn=self.state_machine_arn, maxResults=20)[
                "executions"
            ]
            reports = self._report_index()
            runs = []
            for e in execs:
                detail = self.sfn.describe_execution(executionArn=e["executionArn"])
                out = json.loads(detail.get("output") or "{}")
                started, stopped = e["startDate"], e.get("stopDate")
                run_id = out.get("run_id") or (
                    started.strftime("%Y%m%dT%H%M%S") + f"{started.microsecond // 1000:03d}Z"
                )
                extract = out.get("extract_results") or []
                glue_id = (out.get("clean") or {}).get("glue_run_id")
                dpu_s = None
                if glue_id and self.glue is not None:
                    try:
                        jr = self.glue.get_job_run(JobName=self.glue_job_name, RunId=glue_id)["JobRun"]
                        dpu_s = jr.get("DPUSeconds")
                    except Exception as exc:
                        log(logger, "glue run lookup failed", error=repr(exc))
                rep = reports.get(run_id) or {}
                stats = rep.get("stats") or {}
                runs.append(
                    {
                        "run_id": run_id,
                        "status": e["status"],
                        "started_at": started.isoformat(),
                        "duration_s": round((stopped - started).total_seconds(), 1) if stopped else None,
                        "bytes_ingested": out.get("bytes_copied"),
                        "files_copied": out.get("files_copied"),
                        "files_skipped": out.get("files_skipped"),
                        "frames_extracted": sum(r.get("frames", 0) for r in extract) if extract else None,
                        "clean_rows": stats.get("clean_frames"),
                        "clean_video_frames": stats.get("clean_video_frames"),
                        "quarantined": sum((stats.get("quarantine_by_reason") or {}).values())
                        if stats
                        else None,
                        "verdict": rep.get("verdict"),
                        "glue_dpu_seconds": dpu_s,
                        "est_glue_cost_usd": round(dpu_s / 3600 * GLUE_USD_PER_DPU_HOUR, 4)
                        if dpu_s
                        else None,
                    }
                )
            done = [r for r in runs if r["status"] != "RUNNING"]
            ok = [r for r in done if r["status"] == "SUCCEEDED"]
            durations = [r["duration_s"] for r in ok if r["duration_s"]]

            def total(key: str) -> float:
                return sum(r[key] or 0 for r in runs)

            return {
                "dataset": self.dataset,
                "summary": {
                    "runs": len(runs),
                    "succeeded": len(ok),
                    "failed": len(done) - len(ok),
                    "running": len(runs) - len(done),
                    "success_rate": round(len(ok) / len(done), 3) if done else None,
                    "avg_success_duration_s": round(sum(durations) / len(durations), 1)
                    if durations
                    else None,
                    "mb_ingested": round(total("bytes_ingested") / 1e6, 2),
                    "frames_extracted": int(total("frames_extracted")),
                    "clean_rows_latest": next((r["clean_rows"] for r in runs if r["clean_rows"]), None),
                    "quarantined_total": int(total("quarantined")),
                    "est_glue_cost_usd": round(total("est_glue_cost_usd"), 3),
                },
                "runs": runs,
            }

        return self.cache.get_or_set("metrics", _load)

    def start_run(self, admin_key: str | None) -> dict:
        expected = self.admin_key_loader() if self.admin_key_loader else None
        if not expected or not admin_key or not hmac.compare_digest(admin_key, expected):
            raise ForbiddenError("a valid admin key is required to start a run")
        execs = self.sfn.list_executions(stateMachineArn=self.state_machine_arn, maxResults=50)["executions"]
        if any(e["status"] == "RUNNING" for e in execs):
            raise ConflictError("a run is already in progress")
        today = datetime.now(UTC).date()
        if sum(1 for e in execs if e["startDate"].astimezone(UTC).date() == today) >= self.max_runs_per_day:
            raise TooManyRunsError(f"daily limit of {self.max_runs_per_day} runs reached (cost guardrail)")
        resp = self.sfn.start_execution(
            stateMachineArn=self.state_machine_arn,
            input=json.dumps({"dataset": self.dataset, "max_videos": self.max_videos}),
        )
        self.cache = TTLCache(self.cache.ttl_s)  # new run: drop cached run lists and metrics
        log(logger, "run started from dashboard", execution=resp["executionArn"])
        return {"started": True, "started_at": resp["startDate"].isoformat()}

    # ---------- helpers ----------

    def _report_key(self, run_id: str) -> str:
        return f"reports/quality/dataset={paths.dataset_slug(self.dataset)}/run_id={run_id}.json"

    def _report_index(self) -> dict[str, dict]:
        prefix = f"reports/quality/dataset={paths.dataset_slug(self.dataset)}/"
        out: dict[str, dict] = {}
        resp = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix, MaxKeys=100)
        for obj in resp.get("Contents", []):
            m = re.search(r"run_id=([0-9A-Za-z]+)\.json$", obj["Key"])
            if not m:
                continue
            try:
                out[m.group(1)] = json.loads(
                    self.s3.get_object(Bucket=self.bucket, Key=obj["Key"])["Body"].read()
                )
            except Exception as exc:
                log(logger, "unreadable report", key=obj["Key"], error=repr(exc))
        return out


ROUTES = [
    ("GET", re.compile(r"^/health$"), "health"),
    ("GET", re.compile(r"^/runs$"), "list_runs"),
    ("POST", re.compile(r"^/runs$"), "start_run"),
    ("GET", re.compile(r"^/metrics$"), "metrics"),
    ("GET", re.compile(r"^/runs/(?P<run_id>[^/]+)$"), "run_report"),
    ("GET", re.compile(r"^/runs/(?P<run_id>[^/]+)/episodes$"), "run_episodes"),
    ("GET", re.compile(r"^/runs/(?P<run_id>[^/]+)/frames$"), "run_frames"),
]


def dispatch(
    api: CatalogApi,
    method: str,
    path: str,
    query: dict[str, str] | None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    query, headers = query or {}, {k.lower(): v for k, v in (headers or {}).items()}
    matched = [(m, p.match(path), name) for m, p, name in ROUTES if p.match(path)]
    if not matched:
        return 404, {"error": "not found"}
    for m, match, name in matched:
        if method != m:
            continue
        try:
            args = match.groupdict()
            if name == "run_frames":
                args["limit"] = bounded_int(query.get("limit"), 12, 1, 48)
            if name == "start_run":
                return 202, api.start_run(headers.get("x-admin-key"))
            return 200, getattr(api, name)(**args)
        except BadRequestError as exc:
            return 400, {"error": str(exc)}
        except ForbiddenError as exc:
            return 403, {"error": str(exc)}
        except NotFoundError as exc:
            return 404, {"error": str(exc)}
        except ConflictError as exc:
            return 409, {"error": str(exc)}
        except TooManyRunsError as exc:
            return 429, {"error": str(exc)}
    return 405, {"error": "method not allowed"}
