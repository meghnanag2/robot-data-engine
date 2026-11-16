# Robot Data Engine

Robots learn from demonstrations: recordings of a task being done, with the robot's joint positions,
actions and camera video at every step. Before any of that can train a model, someone has to answer a boring
but critical question: **is this data actually good?** Duplicate frames, broken episodes, gaps in time and
blurry video all quietly make a model worse.

Robot Data Engine is a pipeline on AWS that answers that question automatically. It pulls real robot
demonstrations from Hugging Face (LeRobot PushT), pulls frames out of the video, cleans everything with
Spark, and stores the result as versioned tables that a training job can read. Every rejected row is kept
with the reason it was rejected, and every run is reviewed before its output is trusted.

**Live dashboard:** https://d16oog6749lx6d.cloudfront.net

![Dashboard](docs/screenshots/17-dashboard-metrics.jpg)

## How the data flows

```
┌────────────────────────────┐
│ Hugging Face Hub           │
│ LeRobot PushT demos        │
└──────────────┬─────────────┘
               │  time series (parquet) + video (mp4)
               ▼
┌────────────────────────────┐
│ 1. Ingest                  │
│ copy files to S3 raw/      │
└──────────────┬─────────────┘
               │
               ▼
┌────────────────────────────┐
│ 2. Extract frames          │
│ 1 frame/sec, scored for    │
│ blur, contrast, duplicates │
└──────────────┬─────────────┘
               │
               ▼
┌────────────────────────────┐            ┌────────────────────────┐
│ 3. Clean (PySpark on Glue) │─── bad ───▶│ Quarantine             │
│ rules on episodes + frames │            │ rejected rows + reason │
└──────────────┬─────────────┘            └────────────────────────┘
               │  good
               ▼
┌────────────────────────────┐
│ 4. Iceberg tables          │
│ episodes and frames on S3  │
└──────────────┬─────────────┘
               │
               ▼
┌────────────────────────────┐
│ 5. Quality review          │
│ ok / warn / fail           │
└──────────────┬─────────────┘
               │
               ▼
┌────────────────────────────┐            ┌────────────────────────┐
│ API Gateway + Lambda       │◀───────────│ Dashboard              │
│ reads tables with Athena   │            │ run metrics + frames   │
└────────────────────────────┘            └────────────────────────┘
```

Steps 1 to 5 run as one Step Functions workflow, started on demand from the dashboard or GitHub Actions.

## What gets cleaned

- **Episodes:** missing or NaN values, duplicate timesteps, out-of-range joint states, gaps in time or
  frame numbers, episodes too short to learn from. Sudden action jumps are flagged, not dropped.
- **Frames:** too dark or washed out, blurry, near-duplicates, and frames that don't belong to any episode.

## What a run produces

On the PushT dataset, one run cleans 25,650 timesteps across 206 episodes, extracts 2,565 frames from the
video, and quarantines 1,522 near-duplicate frames, leaving 1,043 clean frames. It takes about 4 minutes.

## Built with

Python, PySpark, AWS (S3, Lambda, Glue, Step Functions, Athena, API Gateway, CloudFront), Apache Iceberg,
PyAV, Terraform, CloudFormation and GitHub Actions.

## Repository

| Path | What |
| --- | --- |
| `src/rde/` | The pipeline code: ingest, frame extraction, cleaning rules, Spark jobs, API |
| `jobs/glue/` | Spark job entry point |
| `infra/` | Terraform for every AWS resource |
| `web/` | Dashboard |
| `bootstrap/` | One-time AWS setup |
| `tests/` | Unit and Spark tests |

Curious how it was built, including what broke along the way? See [docs/BUILD_LOG.md](docs/BUILD_LOG.md).
Setup and operations are in [docs/runbook.md](docs/runbook.md).
