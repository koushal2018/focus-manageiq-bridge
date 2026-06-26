# =============================================================
# Providers + remote state. ENBD fills the backend with their existing
# Terraform state bucket + lock table before `terraform init`.
# =============================================================

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    # ROSA via the Red Hat Cloud Services provider (optional — many teams
    # provision ROSA with the `rosa` CLI instead; see rosa.tf).
    rhcs = {
      source  = "terraform-redhat/rhcs"
      version = "~> 1.6"
    }
  }

  # ---- Remote state (ENBD supplies real values) ----
  # backend "s3" {
  #   bucket         = "enbd-terraform-state"
  #   key            = "finops/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "enbd-terraform-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = var.tags
  }
}

# rhcs token comes from RHCS_TOKEN env or ~/.config/ocm; never commit it.
provider "rhcs" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = "${var.project}-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, var.az_count)
}
