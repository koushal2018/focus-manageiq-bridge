# =============================================================
# Aurora PostgreSQL — the FOCUS warehouse. Same schema as the PoC
# (db/schema.sql); the loader runs in network mode against this endpoint
# (GOTCHA P-6). KMS at rest, credentials in Secrets Manager (G-1), no
# public access.
# =============================================================

resource "aws_kms_key" "rds" {
  description             = "${local.name} RDS encryption"
  deletion_window_in_days = 14
  enable_key_rotation     = true
  tags                    = { Name = "${local.name}-rds-kms" }
}

resource "aws_db_subnet_group" "this" {
  name       = "${local.name}-db-subnets"
  subnet_ids = aws_subnet.private[*].id
  tags       = { Name = "${local.name}-db-subnets" }
}

# Random master password, stored in Secrets Manager. The app reads it at
# runtime; it is never rendered into an image or manifest.
resource "random_password" "db" {
  length  = 28
  special = false # avoid psql/URL escaping surprises in the connection string
}

resource "aws_secretsmanager_secret" "db" {
  name        = "${local.name}/focus-db"
  description = "FOCUS Aurora master credentials"
  kms_key_id  = aws_kms_key.rds.arn
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    username = var.db_username
    password = random_password.db.result
    dbname   = var.db_name
    host     = aws_rds_cluster.focus.endpoint
    port     = 5432
  })
}

resource "aws_rds_cluster" "focus" {
  cluster_identifier      = "${local.name}-focus"
  engine                  = "aurora-postgresql"
  engine_version          = "16.4"
  database_name           = var.db_name
  master_username         = var.db_username
  master_password         = random_password.db.result
  db_subnet_group_name    = aws_db_subnet_group.this.name
  vpc_security_group_ids  = [aws_security_group.db.id]
  storage_encrypted       = true
  kms_key_id              = aws_kms_key.rds.arn
  # IAM DB auth (CKV_AWS_162): lets the app exchange its task/pod role for a
  # short-lived token instead of the master password once AnyBank wires it up.
  iam_database_authentication_enabled = true
  backup_retention_period = var.environment == "prod" ? 14 : 3
  deletion_protection     = var.db_deletion_protection
  skip_final_snapshot     = var.environment != "prod"
  final_snapshot_identifier = var.environment == "prod" ? "${local.name}-focus-final" : null
  tags                    = { Name = "${local.name}-focus" }
}

resource "aws_rds_cluster_instance" "focus" {
  # 1 instance for pilot; 2 (writer+reader across AZs) when multi_az.
  count               = var.db_multi_az ? 2 : 1
  identifier          = "${local.name}-focus-${count.index}"
  cluster_identifier  = aws_rds_cluster.focus.id
  instance_class      = var.db_instance_class
  engine              = aws_rds_cluster.focus.engine
  engine_version      = aws_rds_cluster.focus.engine_version
  publicly_accessible = false
  # Performance Insights (CKV_AWS_353): free at 7-day retention; encrypt with
  # the same CMK as the cluster storage.
  performance_insights_enabled    = true
  performance_insights_kms_key_id = aws_kms_key.rds.arn
  tags                = { Name = "${local.name}-focus-${count.index}" }
}
