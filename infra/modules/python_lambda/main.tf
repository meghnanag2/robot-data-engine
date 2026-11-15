# Reusable Lambda: function + log group + error alarm. Every Lambda in the project uses this.

variable "name" { type = string }
variable "handler" { type = string }
variable "role_arn" { type = string }
variable "zip_path" { type = string }
variable "layers" {
  type    = list(string)
  default = []
}
variable "memory_mb" {
  type    = number
  default = 1024
}
variable "timeout_s" {
  type    = number
  default = 900
}
variable "ephemeral_mb" {
  type    = number
  default = 512
}
variable "env_vars" {
  type    = map(string)
  default = {}
}
variable "alarm_topic_arn" {
  type    = string
  default = null
}
variable "log_retention_days" {
  type    = number
  default = 14
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "this" {
  function_name    = var.name
  role             = var.role_arn
  runtime          = "python3.12"
  architectures    = ["x86_64"]
  handler          = var.handler
  filename         = var.zip_path
  source_code_hash = filebase64sha256(var.zip_path)
  layers           = var.layers
  memory_size      = var.memory_mb
  timeout          = var.timeout_s
  ephemeral_storage { size = var.ephemeral_mb }
  environment { variables = var.env_vars }
  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.this.name
  }
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${var.name}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.this.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_topic_arn == null ? [] : [var.alarm_topic_arn]
}

output "arn" { value = aws_lambda_function.this.arn }
output "name" { value = aws_lambda_function.this.function_name }
output "log_group_arn" { value = aws_cloudwatch_log_group.this.arn }
