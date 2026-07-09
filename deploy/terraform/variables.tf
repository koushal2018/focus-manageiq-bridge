# =============================================================
# Input variables. Pilot defaults shown; production overrides region
# to me-central-1 (GOTCHA P-1) and sizes RDS up.
# =============================================================

variable "region" {
  description = "AWS region. Pilot: us-east-1. Production (real data): me-central-1 (P-1)."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for all resources."
  type        = string
  default     = "anybank-finops"
}

variable "environment" {
  description = "Deployment environment."
  type        = string
  default     = "pilot"
  validation {
    condition     = contains(["pilot", "prod"], var.environment)
    error_message = "environment must be 'pilot' or 'prod'."
  }
}

variable "vpc_cidr" {
  description = "VPC CIDR. Adjust to fit AnyBank's IPAM allocation."
  type        = string
  default     = "10.60.0.0/16"
}

variable "az_count" {
  description = "Number of AZs to span (Multi-AZ RDS + ROSA need >= 2; prod uses 3)."
  type        = number
  default     = 3
}

# --- RDS ---
variable "db_name" {
  type    = string
  default = "focus"
}

variable "db_username" {
  type    = string
  default = "focus_app"
}

variable "db_instance_class" {
  description = "Aurora instance class. Pilot: db.t4g.medium. Prod: db.r6g.large+."
  type        = string
  default     = "db.t4g.medium"
}

variable "db_multi_az" {
  description = "Multi-AZ writer+reader. Pilot may run single; prod must be true."
  type        = bool
  default     = true
}

variable "db_deletion_protection" {
  description = "Block accidental DB deletion. true in prod."
  type        = bool
  default     = false
}

# --- ROSA ---
variable "rosa_cluster_name" {
  type    = string
  default = "anybank-finops"
}

variable "rosa_compute_nodes" {
  description = "Worker node count. A small internal dashboard needs few."
  type        = number
  default     = 2
}

variable "rosa_compute_machine_type" {
  type    = string
  default = "m6i.xlarge"
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default = {
    project    = "anybank-finops"
    managed-by = "terraform"
    data-class = "synthetic-pilot" # set to 'confidential' for prod real data
  }
}
