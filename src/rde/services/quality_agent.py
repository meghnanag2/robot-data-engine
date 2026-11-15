"""AI data-quality agent: reviews each pipeline run and reports.

Read-only by design: it queries Athena, asks an LLM (Claude on Amazon Bedrock) for a verdict
and a short report, saves the report to S3 and emails it. It never modifies data.
If the model call fails, a rule-based verdict is used so the pipeline never depends on the LLM.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from rde.observability import get_logger, log

logger = get_logger(__name__)

VERDICTS = ("ok", "warn", "fail")


@dataclass
class RunStats:
    run_id: str
    dataset: str
    clean_frames: int
    episodes_clean: int
    episodes_rejected: int
    clean_video_frames: int
    quarantine_by_reason: dict[str, int]
    previous: dict[str, Any] | None = None

    @property
    def episode_reject_rate(self) -> float:
        total = self.episodes_clean + self.episodes_rejected
        return self.episodes_rejected / total if total else 1.0


@dataclass
class Verdict:
    verdict: str
    summary: str
    findings: list[str]
    source: str  # "llm" or "rules"


def rule_verdict(stats: RunStats, max_reject_rate: float = 0.2) -> Verdict:
    findings = []
    if stats.clean_frames == 0 or stats.clean_video_frames == 0:
        findings.append("No clean data produced.")
    if stats.episode_reject_rate > max_reject_rate:
        findings.append(
            f"Episode reject rate {stats.episode_reject_rate:.0%} is above {max_reject_rate:.0%}."
        )
    verdict = "fail" if findings else "ok"
    summary = (
        "; ".join(findings)
        or f"{stats.clean_frames} clean frames, reject rate {stats.episode_reject_rate:.0%}."
    )
    return Verdict(verdict, summary, findings, "rules")


PROMPT = """You review data-quality results for a robot learning data pipeline.
Given this run's statistics and the previous run's (may be null), return ONLY JSON:
{{"verdict": "ok" | "warn" | "fail", "summary": "<2 sentences>", "findings": ["<short finding>", ...]}}
Use "fail" if no clean data was produced or the episode reject rate is above {max_rate:.0%};
"warn" for large changes versus the previous run or a single reason dominating the quarantine.

Run statistics:
{stats}
"""


def parse_llm_json(text: str) -> Verdict:
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start : end + 1])
    if data.get("verdict") not in VERDICTS:
        raise ValueError(f"bad verdict: {data.get('verdict')}")
    return Verdict(
        data["verdict"], str(data.get("summary", "")), [str(f) for f in data.get("findings", [])], "llm"
    )


class AthenaStats:
    """Collects run statistics from the Iceberg tables through Athena."""

    def __init__(self, athena: Any, workgroup: str, database: str) -> None:
        self.athena, self.workgroup, self.database = athena, workgroup, database

    def query(self, sql: str) -> list[dict[str, str]]:
        qid = self.athena.start_query_execution(
            QueryString=sql, WorkGroup=self.workgroup, QueryExecutionContext={"Database": self.database}
        )["QueryExecutionId"]
        for _ in range(120):
            state = self.athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
            if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            time.sleep(1)
        if state != "SUCCEEDED":
            raise RuntimeError(f"Athena query {qid} ended {state}")
        rows = self.athena.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
        header = [c.get("VarCharValue") for c in rows[0]["Data"]]
        return [dict(zip(header, [c.get("VarCharValue") for c in r["Data"]], strict=False)) for r in rows[1:]]

    def run_stats(self, dataset: str, run_id: str) -> RunStats:
        where = f"dataset = '{dataset}' AND run_id = '{run_id}'"
        one = self.query(f"""
            SELECT
              (SELECT count(*) FROM episode_frames WHERE {where}) AS clean_frames,
              (SELECT count(*) FROM episodes WHERE {where} AND status = 'clean') AS episodes_clean,
              (SELECT count(*) FROM episodes WHERE {where} AND status = 'rejected') AS episodes_rejected,
              (SELECT count(*) FROM video_frames WHERE {where}) AS clean_video_frames
        """)[0]  # noqa: S608
        reasons = self.query(f"SELECT reason, count(*) AS n FROM quarantine WHERE {where} GROUP BY reason")  # noqa: S608
        prev = self.query(f"""
            SELECT run_id, count(*) AS clean_frames FROM episode_frames
            WHERE dataset = '{dataset}' AND run_id < '{run_id}'
            GROUP BY run_id ORDER BY run_id DESC LIMIT 1
        """)  # noqa: S608
        return RunStats(
            run_id=run_id,
            dataset=dataset,
            clean_frames=int(one["clean_frames"]),
            episodes_clean=int(one["episodes_clean"]),
            episodes_rejected=int(one["episodes_rejected"]),
            clean_video_frames=int(one["clean_video_frames"]),
            quarantine_by_reason={r["reason"]: int(r["n"]) for r in reasons},
            previous=prev[0] if prev else None,
        )


class QualityAgent:
    def __init__(self, stats_source: Any, bedrock: Any, model_id: str, max_reject_rate: float = 0.2) -> None:
        self.stats_source, self.bedrock, self.model_id = stats_source, bedrock, model_id
        self.max_reject_rate = max_reject_rate

    def ask_llm(self, stats: RunStats) -> Verdict:
        prompt = PROMPT.format(max_rate=self.max_reject_rate, stats=json.dumps(asdict(stats), indent=2))
        resp = self.bedrock.converse(
            modelId=self.model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 400, "temperature": 0},
        )
        return parse_llm_json(resp["output"]["message"]["content"][0]["text"])

    def review(self, dataset: str, run_id: str) -> dict:
        stats = self.stats_source.run_stats(dataset, run_id)
        try:
            verdict = self.ask_llm(stats)
            # the model may warn, but it can never pass a run that breaks the hard rules
            hard = rule_verdict(stats, self.max_reject_rate)
            if hard.verdict == "fail":
                verdict = Verdict("fail", verdict.summary, hard.findings + verdict.findings, "llm+rules")
        except Exception as exc:
            log(logger, "llm unavailable, using rules", error=repr(exc))
            verdict = rule_verdict(stats, self.max_reject_rate)
        report = {
            "stats": asdict(stats),
            "reject_rate": round(stats.episode_reject_rate, 4),
            **asdict(verdict),
        }
        log(logger, "quality review", run_id=run_id, verdict=verdict.verdict, source=verdict.source)
        return report
