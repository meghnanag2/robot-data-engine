# ---------- Read-only catalog API: API Gateway (HTTP API) -> Lambda ----------
# Pay per request: $0 when nobody is using it.

module "api" {
  source          = "./modules/python_lambda"
  name            = "${local.name}-api"
  handler         = "rde.handlers.api_handler.handler"
  role_arn        = aws_iam_role.api.arn
  zip_path        = var.lambda_zip
  memory_mb       = 512
  timeout_s       = 29 # API Gateway's own limit is 30 s
  alarm_topic_arn = aws_sns_topic.alerts.arn
  env_vars = {
    RDE_ENV           = var.env
    DATA_BUCKET       = aws_s3_bucket.data.bucket
    ATHENA_WORKGROUP  = aws_athena_workgroup.wg.name
    GLUE_DATABASE     = aws_glue_catalog_database.db.name
    STATE_MACHINE_ARN = aws_sfn_state_machine.pipeline.arn
    DATASET           = var.dataset
    GLUE_JOB_NAME     = aws_glue_job.clean.name
    ADMIN_KEY_PARAM   = aws_ssm_parameter.admin_key.name
    MAX_RUNS_PER_DAY  = tostring(var.max_runs_per_day)
  }
}

# Admin key for the dashboard's "Run pipeline" button: generated once, stored encrypted in SSM.
# Read it with: aws ssm get-parameter --name /rde/<env>/admin-key --with-decryption
resource "random_password" "admin_key" {
  length  = 32
  special = false
}

resource "aws_ssm_parameter" "admin_key" {
  name        = "/rde/${var.env}/admin-key"
  description = "Admin key for starting pipeline runs from the dashboard"
  type        = "SecureString"
  value       = random_password.admin_key.result
}

resource "aws_apigatewayv2_api" "http" {
  name          = "${local.name}-api"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = ["https://${aws_cloudfront_distribution.web.domain_name}"]
    allow_methods = ["GET", "POST"]
    allow_headers = ["content-type", "x-admin-key"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.api.arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "get_any" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_route" "start_run" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "POST /runs"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_rate_limit  = 10 # requests per second: protects the bill from abuse
    throttling_burst_limit = 20
  }
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = module.api.name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}

# ---------- API role: read reports, frames and tables; list runs. Nothing else. ----------

resource "aws_iam_role" "api" {
  name               = "${local.name}-api-lambda"
  assume_role_policy = data.aws_iam_policy_document.assume["lambda"].json
}

resource "aws_iam_role_policy" "api" {
  role = aws_iam_role.api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["states:ListExecutions", "states:StartExecution"], Resource = aws_sfn_state_machine.pipeline.arn },
      { Effect = "Allow", Action = ["states:DescribeExecution"], Resource = "arn:aws:states:${var.region}:${data.aws_caller_identity.me.account_id}:execution:${aws_sfn_state_machine.pipeline.name}:*" },
      { Effect = "Allow", Action = ["glue:GetJobRun"], Resource = aws_glue_job.clean.arn },
      { Effect = "Allow", Action = ["ssm:GetParameter"], Resource = aws_ssm_parameter.admin_key.arn },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = [
        "${aws_s3_bucket.data.arn}/reports/*",
        "${aws_s3_bucket.data.arn}/processed/frames/*",
        "${aws_s3_bucket.data.arn}/warehouse/*",
      ] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.data.arn}/athena-results/*" },
      { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = aws_s3_bucket.data.arn },
      { Effect = "Allow", Action = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults"],
      Resource = aws_athena_workgroup.wg.arn },
      { Effect = "Allow", Action = ["glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:GetPartitions"],
        Resource = [
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:catalog",
          aws_glue_catalog_database.db.arn,
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:table/${local.db}/*",
      ] },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${module.api.log_group_arn}:*" },
    ]
  })
}
