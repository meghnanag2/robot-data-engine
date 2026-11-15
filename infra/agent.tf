# ---------- AI quality agent ----------

module "quality_agent" {
  source    = "./modules/python_lambda"
  name      = "${local.name}-quality-agent"
  handler   = "rde.handlers.quality_agent_handler.handler"
  role_arn  = aws_iam_role.agent.arn
  alarm_topic_arn = aws_sns_topic.alerts.arn
  zip_path  = var.lambda_zip
  memory_mb = 512
  timeout_s = 300
  env_vars = {
    RDE_ENV          = var.env
    DATA_BUCKET      = aws_s3_bucket.data.bucket
    ATHENA_WORKGROUP = aws_athena_workgroup.wg.name
    GLUE_DATABASE    = aws_glue_catalog_database.db.name
    BEDROCK_MODEL_ID = var.bedrock_model_id
    MAX_REJECT_RATE  = tostring(var.max_reject_rate)
    ALERT_TOPIC_ARN  = aws_sns_topic.alerts.arn
  }
}

# ---------- alerts: quality reports + every alarm ----------

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
