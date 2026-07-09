# =============================================================
# ROSA (Red Hat OpenShift Service on AWS) — the web tier host.
# Chosen over EKS because AnyBank is an OpenShift shop and ManageIQ is Red Hat
# lineage (GOTCHA P-2). The same PoC container image runs here unchanged.
#
# IMPORTANT — two provisioning paths; pick one with AnyBank's platform team:
#
#   (A) rosa CLI / OCM (most common, recommended for first cluster):
#       rosa create account-roles --mode auto
#       rosa create cluster \
#         --cluster-name <var.rosa_cluster_name> \
#         --sts --mode auto \
#         --region <var.region> \
#         --version <pinned> \
#         --compute-machine-type <var.rosa_compute_machine_type> \
#         --replicas <var.rosa_compute_nodes> \
#         --subnet-ids <private subnet ids from this VPC> \
#         --private-link            # private cluster, bank posture
#       The cluster lands in THIS VPC's private subnets (network.tf).
#
#   (B) terraform-redhat/rhcs provider (if AnyBank standardizes ROSA in TF):
#       the rhcs_cluster_rosa_hcp resource below is a skeleton to fill.
#       Requires RHCS_TOKEN + the account/operator roles created first.
#
# This file ships path (B) as a commented skeleton so the intent is in
# code, while leaving the live choice to the platform team. Provisioning
# ROSA fully in Terraform is involved (STS roles, OIDC, operator roles);
# the rosa CLI automates that and is the pragmatic path for a pilot.
# =============================================================

# Example STS / account-role wiring is created by `rosa create account-roles`.
# When using path (B), reference those role ARNs here.

# resource "rhcs_cluster_rosa_hcp" "this" {
#   name               = var.rosa_cluster_name
#   cloud_region       = var.region
#   aws_account_id     = data.aws_caller_identity.current.account_id
#   aws_billing_account_id = data.aws_caller_identity.current.account_id
#   availability_zones = local.azs
#   replicas           = var.rosa_compute_nodes
#   compute_machine_type = var.rosa_compute_machine_type
#   aws_subnet_ids     = aws_subnet.private[*].id
#   private            = true   # bank posture: private cluster
#   sts = {
#     # role ARNs from `rosa create account-roles` / `create operator-roles`
#   }
#   properties = { rosa_creator_arn = data.aws_caller_identity.current.arn }
# }

data "aws_caller_identity" "current" {}

# A note for whoever applies this: the app (Helm chart in ../openshift) is
# deployed AFTER the cluster exists, with `oc login` + `helm upgrade`.
# Terraform provisions the infra; the app lifecycle is OpenShift-native.
