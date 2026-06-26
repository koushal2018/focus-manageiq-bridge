# =============================================================
# ECR repository for the web image. The same Dockerfile from the repo root
# builds it; a pipeline (or `make push`) tags and pushes here, and the Helm
# chart pulls from it.
# =============================================================

resource "aws_ecr_repository" "web" {
  name                 = "${var.project}/web"
  image_tag_mutability = "IMMUTABLE" # pin by digest/tag; no silent overwrites
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "KMS"
  }
  tags = { Name = "${local.name}-web-ecr" }
}

# Keep only the last 15 images to bound storage cost.
resource "aws_ecr_lifecycle_policy" "web" {
  repository = aws_ecr_repository.web.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 15 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 15
      }
      action = { type = "expire" }
    }]
  })
}
