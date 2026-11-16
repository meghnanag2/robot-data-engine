# ---------- Dashboard website: private S3 bucket served through CloudFront (HTTPS) ----------
# CloudFront's always-free tier (1 TB + 10M requests a month) covers portfolio traffic.

resource "aws_s3_bucket" "web" {
  bucket        = "${local.name}-web-${data.aws_caller_identity.me.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${local.name}-web"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  comment             = "${local.name} robot data engine dashboard"
  default_root_object = "index.html"
  price_class         = "PriceClass_100" # US/Canada/Europe edges only: cheapest

  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  default_cache_behavior {
    target_origin_id       = "web"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = "658327ea-f89d-4fab-a63d-7e88639e58f6" # AWS managed CachingOptimized
    compress               = true
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# Only this CloudFront distribution may read the bucket.
resource "aws_s3_bucket_policy" "web" {
  bucket = aws_s3_bucket.web.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.web.arn}/*"
      Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.web.arn } }
    }]
  })
}

locals {
  web_dir = "${path.module}/../web"
  web_types = {
    html = "text/html; charset=utf-8"
    css  = "text/css; charset=utf-8"
    js   = "application/javascript; charset=utf-8"
    svg  = "image/svg+xml"
  }
}

resource "aws_s3_object" "web_files" {
  for_each      = fileset(local.web_dir, "**/*.{html,css,js,svg}")
  bucket        = aws_s3_bucket.web.id
  key           = each.value
  source        = "${local.web_dir}/${each.value}"
  etag          = filemd5("${local.web_dir}/${each.value}")
  content_type  = local.web_types[reverse(split(".", each.value))[0]]
  cache_control = "max-age=60"
}

# The API URL is injected at deploy time, so the same site works in dev and prod.
resource "aws_s3_object" "web_config" {
  bucket        = aws_s3_bucket.web.id
  key           = "config.js"
  content       = "window.RDE_CONFIG = ${jsonencode({ apiUrl = aws_apigatewayv2_api.http.api_endpoint, env = var.env, repo = "https://github.com/meghnanag2/robot-data-engine" })};"
  content_type  = "application/javascript; charset=utf-8"
  cache_control = "max-age=60"
}
