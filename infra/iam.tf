# One least-privilege role per service. Nothing shares a role.

data "aws_iam_policy_document" "assume" {
  for_each = {
    lambda    = "lambda.amazonaws.com"
    glue      = "glue.amazonaws.com"
    states    = "states.amazonaws.com"
    scheduler = "scheduler.amazonaws.com"
  }
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = [each.value]
    }
  }
}

# ---------- ingest Lambda: write raw/ only ----------

resource "aws_iam_role" "ingest" {
  name               = "${local.name}-ingest-lambda"
  assume_role_policy = data.aws_iam_policy_document.assume["lambda"].json
}

resource "aws_iam_role_policy" "ingest" {
  role = aws_iam_role.ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:PutObject", "s3:GetObject"], Resource = "${aws_s3_bucket.data.arn}/raw/*" },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.data.arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${module.ingest.log_group_arn}:*" },
    ]
  })
}

# ---------- extract Lambda: read raw/, write processed/ ----------

resource "aws_iam_role" "extract" {
  name               = "${local.name}-extract-lambda"
  assume_role_policy = data.aws_iam_policy_document.assume["lambda"].json
}

resource "aws_iam_role_policy" "extract" {
  role = aws_iam_role.extract.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.data.arn}/raw/*" },
      { Effect = "Allow", Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.data.arn}/processed/*" },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${module.extract.log_group_arn}:*" },
    ]
  })
}

# ---------- Glue job: read raw/ + processed/, write warehouse/, own catalog db ----------

resource "aws_iam_role" "glue" {
  name               = "${local.name}-glue-job"
  assume_role_policy = data.aws_iam_policy_document.assume["glue"].json
}

resource "aws_iam_role_policy" "glue" {
  role = aws_iam_role.glue.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = [
        "${aws_s3_bucket.data.arn}/raw/*", "${aws_s3_bucket.data.arn}/processed/*", "${aws_s3_bucket.data.arn}/artifacts/*"] },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = [
        "${aws_s3_bucket.data.arn}/warehouse/*", "${aws_s3_bucket.data.arn}/artifacts/tmp/*"] },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.data.arn },
      { Effect = "Allow", Action = [
        "glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:CreateTable", "glue:UpdateTable",
        "glue:GetPartitions", "glue:BatchGetPartition"],
        Resource = [
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:catalog",
          aws_glue_catalog_database.db.arn,
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:table/${local.db}/*",
      ] },
      { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], Resource = "arn:aws:logs:${var.region}:${data.aws_caller_identity.me.account_id}:log-group:/aws-glue/*" },
      { Effect = "Allow", Action = ["cloudwatch:PutMetricData"], Resource = "*", Condition = { StringEquals = { "cloudwatch:namespace" = "Glue" } } },
    ]
  })
}

# ---------- Step Functions: invoke our two Lambdas + run our one Glue job ----------

resource "aws_iam_role" "states" {
  name               = "${local.name}-pipeline-sfn"
  assume_role_policy = data.aws_iam_policy_document.assume["states"].json
}

resource "aws_iam_role_policy" "states" {
  role = aws_iam_role.states.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = [module.ingest.arn, module.extract.arn, module.quality_agent.arn] },
      { Effect = "Allow", Action = ["glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:BatchStopJobRun"], Resource = aws_glue_job.clean.arn },
    ]
  })
}

# ---------- Scheduler: start this one state machine ----------

resource "aws_iam_role" "scheduler" {
  name               = "${local.name}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.assume["scheduler"].json
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["states:StartExecution"], Resource = aws_sfn_state_machine.pipeline.arn }]
  })
}

# ---------- quality agent: read-only on data, query via Athena, call Bedrock, write reports ----------

resource "aws_iam_role" "agent" {
  name               = "${local.name}-quality-agent"
  assume_role_policy = data.aws_iam_policy_document.assume["lambda"].json
}

resource "aws_iam_role_policy" "agent" {
  role = aws_iam_role.agent.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults"],
      Resource = aws_athena_workgroup.wg.arn },
      { Effect = "Allow", Action = ["glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:GetPartitions"],
        Resource = [
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:catalog",
          aws_glue_catalog_database.db.arn,
          "arn:aws:glue:${var.region}:${data.aws_caller_identity.me.account_id}:table/${local.db}/*",
      ] },
      { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.data.arn}/warehouse/*" },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.data.arn}/athena-results/*" },
      { Effect = "Allow", Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.data.arn}/reports/*" },
      { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = aws_s3_bucket.data.arn },
      { Effect = "Allow", Action = ["bedrock:InvokeModel"], Resource = [
        "arn:aws:bedrock:*::foundation-model/*",
        "arn:aws:bedrock:*:${data.aws_caller_identity.me.account_id}:inference-profile/*",
      ] },
      { Effect = "Allow", Action = ["sns:Publish"], Resource = aws_sns_topic.alerts.arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${module.quality_agent.log_group_arn}:*" },
    ]
  })
}
