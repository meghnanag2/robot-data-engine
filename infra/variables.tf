variable "env" {
  type = string
  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be dev or prod"
  }
}

variable "region" {
  type    = string
  default = "us-west-2"
}

variable "lambda_zip" {
  description = "rde package zip for both Lambdas, built by `make build`"
  type        = string
  default     = "../build/lambda.zip"
}

variable "layer_zip" {
  description = "Lambda layer zip (PyAV, numpy, Pillow), built by `make build`"
  type        = string
  default     = "../build/extract_layer.zip"
}

variable "glue_package_zip" {
  description = "rde package zip for Glue --extra-py-files, built by `make build`"
  type        = string
  default     = "../build/rde.zip"
}

variable "dataset" {
  type    = string
  default = "lerobot/pusht"
}

variable "max_videos" {
  description = "Videos per camera to ingest per run"
  type        = number
  default     = 2
}

variable "enable_schedule" {
  description = "Run the pipeline daily (on in prod, off in dev)"
  type        = bool
  default     = false
}

variable "alert_email" {
  description = "Budget alert email; passed from a GitHub secret, never committed"
  type        = string
  default     = ""
  sensitive   = true
}

variable "monthly_budget_usd" {
  type    = string
  default = "25"
}

variable "cleaning_rules" {
  description = "Thresholds used by the Glue cleaning job"
  type        = map(number)
  default = {
    fps                = 10
    z_max              = 6
    action_spike       = 50
    min_episode_frames = 10
    max_gap_frames     = 1.5
    min_brightness     = 15
    max_brightness     = 245
    min_blur_score     = 10
    min_contrast       = 10
  }
}

variable "bedrock_model_id" {
  description = "Bedrock model or inference profile for the quality agent (confirm in the Bedrock console)"
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "max_reject_rate" {
  description = "Episode reject rate above which the quality agent fails the run"
  type        = number
  default     = 0.2
}

variable "max_runs_per_day" {
  description = "Cost guardrail: runs the dashboard button may start per UTC day"
  type        = number
  default     = 5
}
