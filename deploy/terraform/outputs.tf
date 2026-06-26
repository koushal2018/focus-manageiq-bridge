# =============================================================
# Outputs the app deployment (Helm values) consumes.
# =============================================================

output "region" {
  value = var.region
}

output "vpc_id" {
  value = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Feed these to `rosa create cluster --subnet-ids`."
  value       = aws_subnet.private[*].id
}

output "rds_endpoint" {
  description = "FOCUS_PG_HOST for the app (network mode, P-6)."
  value       = aws_rds_cluster.focus.endpoint
}

output "rds_reader_endpoint" {
  value = aws_rds_cluster.focus.reader_endpoint
}

output "db_secret_arn" {
  description = "Secrets Manager ARN holding FOCUS DB creds. Mount into the pod; never bake (G-1)."
  value       = aws_secretsmanager_secret.db.arn
}

output "ecr_repository_url" {
  description = "Push the web image here; reference it in the Helm values."
  value       = aws_ecr_repository.web.repository_url
}
