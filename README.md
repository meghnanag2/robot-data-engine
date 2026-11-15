# Robot Data Engine

A production-style data pipeline for robot learning data on AWS: it ingests LeRobot demonstrations from the
Hugging Face Hub, extracts and scores video frames, cleans episodes and frames with Spark, writes versioned
Apache Iceberg tables, and has an AI agent review data quality on every run. Everything is deployed by
GitHub Actions through Terraform; nothing is created by hand in the console.

## Architecture

```
EventBridge Scheduler / GitHub Actions
            |
      Step Functions  rde-<env>-pipeline
            |
  1. Ingest (Lambda)            Hugging Face  ->  s3://rde-<env>-<acct>/raw/
  2. Extract (Lambda x4, PyAV)  raw/*.mp4     ->  processed/frames/*.jpg + frame_meta/*.jsonl
  3. Clean (Glue 5.0 Spark)     raw + processed -> Iceberg: episode_frames, episodes, video_frames, quarantine
  4. Quality agent (Lambda + Bedrock)  Athena stats -> verdict ok/warn/fail -> reports/ + SNS email
            |
      Athena (SQL, time travel)       CloudWatch alarms -> SNS
```

## Repository layout

| Path | What |
| --- | --- |
| `src/rde/` | The Python package: `config`, `observability`, `storage`, `sources`, `quality`, `services`, `spark`, `handlers` |
| `jobs/glue/clean_job.py` | Thin Glue entry point; the `rde` package ships to Glue as a zip |
| `infra/` | Terraform: storage, catalog, Lambdas (`modules/python_lambda`), Glue, Step Functions, IAM, alerts, budget |
| `infra/envs/` | `dev.tfvars`, `prod.tfvars` |
| `tests/` | Unit tests; `tests/spark/` runs the real Spark code locally when pyspark is installed |
| `bootstrap/` | One-time AWS setup (state bucket, GitHub OIDC, deploy role) |
| `.github/workflows/` | `ci.yml` (PR), `deploy.yml` (main -> dev -> prod), `run-pipeline.yml` (button + nightly) |
| `sql/checks.sql` | Athena queries to inspect a run |

## Data cleaning rules

Episodes: null or NaN values, duplicate frames, state values beyond 6 std devs, too-short episodes, frame-index
gaps, timestamp gaps. Large action jumps are flagged, not dropped. Frames: too dark, overexposed, blurry
(variance of Laplacian), near-duplicates (average hash), frames that map to no episode. Every rejected row
lands in `quarantine` with a reason code. Thresholds live in `infra/variables.tf` (`cleaning_rules`).

## Environments and deployment

- Pull request: lint, unit tests, `terraform plan` for dev.
- Merge to `main`: build once, deploy dev, smoke test (runs the pipeline), then prod after approval.
- Each environment has its own bucket, database, roles and Terraform state.
- GitHub logs in to AWS with OIDC; no AWS keys are stored anywhere.

## Security

- One IAM role per service, scoped to its own prefixes and resources.
- The deploy role can only manage `rde-*` resources and can only be assumed by this repo.
- Humans sign in through IAM Identity Center (SSO), not the root user.
- The quality agent is read-only on data.

## Cost

About $15 to $25 a month at demo scale, mostly Glue runs. Guardrails: AWS Budget alert, 30-minute Glue
timeout, 10 GB Athena scan cap, 7-day expiry on Athena results, schedule off in dev.

See `docs/runbook.md` for setup and operations.
