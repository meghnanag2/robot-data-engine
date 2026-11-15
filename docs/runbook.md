# Runbook

## One-time setup

1. **SSO for your own login (recommended):** IAM Identity Center -> enable -> create a user for yourself ->
   assign the AdministratorAccess permission set to your account. Use the SSO portal from then on.
2. **Bedrock model access:** Bedrock console -> Model access -> enable the Anthropic model set in
   `bedrock_model_id` (infra/variables.tf). Without it the quality agent uses its rule-based fallback.
3. **Bootstrap (AWS CloudShell, us-west-2), as a CloudFormation stack (`bootstrap/bootstrap.cfn.yaml`); `bootstrap/bootstrap.sh` does the same without CloudFormation:**
   ```
   git clone https://github.com/<owner>/robot-data-engine && cd robot-data-engine
   aws cloudformation deploy --stack-name rde-bootstrap --template-file bootstrap/bootstrap.cfn.yaml \
     --capabilities CAPABILITY_NAMED_IAM --parameter-overrides GitHubRepo=<owner>/robot-data-engine
   aws cloudformation describe-stacks --stack-name rde-bootstrap --query "Stacks[0].Outputs"
   ```
4. **GitHub -> Settings:**
   - Actions variables: `AWS_ROLE_ARN`, `AWS_REGION`, `TF_STATE_BUCKET` (printed by the bootstrap).
   - Actions secret: `ALERT_EMAIL`.
   - Environments: `dev`; `prod` with "Required reviewers" = you.
5. Push to `main` (or re-run the deploy workflow). Confirm the SNS subscription email.

## Running the pipeline

- Automatic: daily in prod (EventBridge Scheduler), nightly in dev (GitHub cron).
- On demand: GitHub -> Actions -> run-pipeline -> Run workflow.

## When something fails

| Symptom | Where to look | Usual fix |
| --- | --- | --- |
| Run failed at Ingest | `/aws/lambda/rde-<env>-ingest` logs | Hugging Face unreachable or rate limited: re-run |
| Extract result `status: error` | `processed/extract_errors/` in S3 | Unsupported codec or corrupt file; the rest of the run continues |
| Glue job failed | Glue console -> job runs -> logs | Schema change upstream: check `meta/info.json`, update `rde/spark` |
| `DataQualityFailed` | Email + `reports/quality/` | Check `quarantine` by reason (sql/checks.sql #3); fix data or rule |
| Budget email | Cost Explorer by service | Usually Glue: lower `number_of_workers` or run less often |

## Reprocessing

Raw data is never modified. Re-running with the same run id overwrites only that run's partitions, so it
is safe. Older table versions stay queryable through Iceberg snapshots.

## Tearing down dev

GitHub Actions or CloudShell with the deploy role: `terraform destroy -var-file=envs/dev.tfvars`.
