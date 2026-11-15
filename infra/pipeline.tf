# Build artifacts come from `make build` (run by CI): build/lambda.zip, build/extract_layer.zip,
# build/rde.zip (package for Glue). Terraform only deploys what CI built.

# ---------- Lambdas ----------

# layer zip is over the 50 MB direct-upload limit, so it goes through S3
resource "aws_s3_object" "layer" {
  bucket = aws_s3_bucket.data.id
  key    = "artifacts/layers/extract_layer-${filemd5(var.layer_zip)}.zip"
  source = var.layer_zip
}

resource "aws_lambda_layer_version" "extract" {
  layer_name          = "${local.name}-pyav-numpy-pillow"
  s3_bucket           = aws_s3_object.layer.bucket
  s3_key              = aws_s3_object.layer.key
  compatible_runtimes = ["python3.12"]
}

module "ingest" {
  source    = "./modules/python_lambda"
  name      = "${local.name}-ingest"
  handler   = "rde.handlers.ingest_handler.handler"
  role_arn  = aws_iam_role.ingest.arn
  alarm_topic_arn = aws_sns_topic.alerts.arn
  zip_path  = var.lambda_zip
  memory_mb = 1024
  env_vars  = { RDE_ENV = var.env, DATA_BUCKET = aws_s3_bucket.data.bucket }
}

module "extract" {
  source       = "./modules/python_lambda"
  name         = "${local.name}-extract"
  handler      = "rde.handlers.extract_handler.handler"
  role_arn     = aws_iam_role.extract.arn
  alarm_topic_arn = aws_sns_topic.alerts.arn
  zip_path     = var.lambda_zip
  layers       = [aws_lambda_layer_version.extract.arn]
  memory_mb    = 2048
  ephemeral_mb = 2048
  env_vars     = { RDE_ENV = var.env, DATA_BUCKET = aws_s3_bucket.data.bucket, SAMPLE_EVERY_S = "1.0" }
}

# ---------- Glue Spark job (Iceberg) ----------

resource "aws_s3_object" "glue_script" {
  bucket = aws_s3_bucket.data.id
  key    = "artifacts/glue/clean_job.py"
  source = "${path.module}/../jobs/glue/clean_job.py"
  etag   = filemd5("${path.module}/../jobs/glue/clean_job.py")
}

resource "aws_s3_object" "glue_package" {
  bucket = aws_s3_bucket.data.id
  key    = "artifacts/glue/rde-${filemd5(var.glue_package_zip)}.zip"
  source = var.glue_package_zip
}

locals {
  iceberg_conf = join(" --conf ", [
    "spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
    "spark.sql.catalog.glue_catalog=org.apache.iceberg.spark.SparkCatalog",
    "spark.sql.catalog.glue_catalog.warehouse=s3://${aws_s3_bucket.data.bucket}/warehouse/",
    "spark.sql.catalog.glue_catalog.catalog-impl=org.apache.iceberg.aws.glue.GlueCatalog",
    "spark.sql.catalog.glue_catalog.io-impl=org.apache.iceberg.aws.s3.S3FileIO",
    "spark.sql.sources.partitionOverwriteMode=dynamic",
  ])
}

resource "aws_glue_job" "clean" {
  name              = "${local.name}-clean"
  role_arn          = aws_iam_role.glue.arn
  glue_version      = "5.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 30 # minutes: hard stop so a bad run cannot burn money
  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${aws_s3_object.glue_script.bucket}/${aws_s3_object.glue_script.key}"
  }
  default_arguments = {
    "--datalake-formats"                 = "iceberg"
    "--conf"                             = local.iceberg_conf
    "--extra-py-files"                   = "s3://${aws_s3_object.glue_package.bucket}/${aws_s3_object.glue_package.key}"
    "--TempDir"                          = "s3://${aws_s3_bucket.data.bucket}/artifacts/tmp/"
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = "true"
    "--DATA_BUCKET"                      = aws_s3_bucket.data.bucket
    "--DATABASE"                         = aws_glue_catalog_database.db.name
    "--DATASET"                          = var.dataset
    "--RUN_ID"                           = "manual"
    "--RULES"                            = jsonencode(var.cleaning_rules)
  }
}

# ---------- Step Functions: ingest -> extract (parallel) -> clean ----------

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${local.name}-pipeline"
  role_arn = aws_iam_role.states.arn
  definition = templatefile("${path.module}/pipeline.asl.json", {
    ingest_arn  = module.ingest.arn
    extract_arn = module.extract.arn
    glue_job    = aws_glue_job.clean.name
    agent_arn   = module.quality_agent.arn
  })
}

# ---------- daily schedule (on in prod, off in dev) ----------

resource "aws_scheduler_schedule" "daily" {
  name                = "${local.name}-daily"
  state               = var.enable_schedule ? "ENABLED" : "DISABLED"
  schedule_expression = "cron(17 10 * * ? *)"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_sfn_state_machine.pipeline.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ dataset = var.dataset, max_videos = var.max_videos })
  }
}

resource "aws_cloudwatch_metric_alarm" "pipeline_failed" {
  alarm_name          = "${local.name}-pipeline-failed"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.pipeline.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
