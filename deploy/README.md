# Production deployment — ROSA + RDS

Infrastructure-as-code for the production target in
[`../docs/production-architecture.md`](../docs/production-architecture.md):
the FinOps console on **ROSA** (Red Hat OpenShift Service on AWS) backed by
**Aurora/RDS PostgreSQL**, fronted by ENBD SSO, private.

> **Authored, not applied.** This was written for ENBD to deploy from a
> credentialed session. It was NOT `terraform apply`'d from the PoC host —
> that instance role has no eks/rds/ecr/iam permissions (GOTCHAS CX-4/CX-5).
> Treat these files as the reviewed starting point, not a turnkey artifact.

## What's here

```
deploy/
  terraform/         AWS infrastructure (HCL)
    main.tf            providers + remote state placeholder
    network.tf         VPC, subnets, endpoints (private posture)
    rds.tf             Aurora PostgreSQL (Multi-AZ), KMS, Secrets Manager
    rosa.tf            ROSA cluster (notes + the rosa-cli path; see comments)
    ecr.tf             container registry for the web image
    variables.tf       region (us-east-1 pilot → me-central-1 prod), sizing
    outputs.tf         endpoints the app deploy consumes
  openshift/         the application on the cluster
    helm/finops/       Helm chart: Deployment, Service, Route, secrets wiring
    README.md          oc/helm apply steps
```

## Order of operations (ENBD, from a credentialed session)

1. **State backend** — set the S3 backend in `terraform/main.tf` (a bucket +
   DynamoDB lock table ENBD already runs for Terraform state).
2. **Region** — confirm `var.region`. Pilot: `us-east-1`. Production with real
   cost data: `me-central-1` (GOTCHA P-1, residency).
3. `terraform init && terraform plan` — review. `terraform apply` provisions
   VPC + RDS + ECR + Secrets Manager.
4. **ROSA** — provision via `rosa create cluster` (see `rosa.tf` comments;
   ROSA is typically created with the `rosa` CLI / OCM, not pure Terraform).
   Or use the Red Hat `rhcs` Terraform provider if ENBD standardizes on it.
5. **Image** — build + push the web image to the ECR repo (`make push` or a
   pipeline). Same `Dockerfile` as the PoC.
6. **App** — `helm upgrade --install finops openshift/helm/finops`
   with values pointing at the RDS endpoint + the Secrets Manager ARN.
7. **SSO + Route** — wire the OpenShift Route to ENBD's IdP (OAuth proxy or
   the platform's existing SSO integration).

## Non-negotiables carried from the PoC

- **No `verify=False`** anywhere (G-6). TLS to RDS uses the RDS CA bundle.
- **Secrets via Secrets Manager**, never baked into images or manifests
  (G-1). `PGPASSWORD` is injected at runtime from a mounted secret.
- **`FOCUS_PG_MODE=network`** in the cluster (P-6) — the same loader code as
  the PoC, no fork.
- **Writable dirs group-writable** for OpenShift's arbitrary-UID SCC (P-7).
- **Region parameterized** — never hardcode us-east-1 into the prod path (P-1).
- **Bedrock optional + fail-closed** — `BEDROCK_DISABLED=1` unless legal
  clears the `global.` inference-profile residency posture (B-1).
