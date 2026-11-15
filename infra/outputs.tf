output "data_bucket" { value = aws_s3_bucket.data.bucket }
output "state_machine_arn" { value = aws_sfn_state_machine.pipeline.arn }
output "glue_database" { value = aws_glue_catalog_database.db.name }
output "athena_workgroup" { value = aws_athena_workgroup.wg.name }
