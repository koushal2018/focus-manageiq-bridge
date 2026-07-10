# CloudFront share — AnyBank FinOps PoC (synthetic)

A CloudFront distribution fronts the EC2-hosted console so it can be shared
with the client and internal peer reviewers over **HTTPS with HTTP Basic Auth
at the edge**. The data is synthetic (`DEMO-*`); the auth gate keeps a
bank-branded demo off the open internet.

> **Live identifiers redacted for the customer share (SEC-8).** Distribution
> ID, origin hostname/IP, and security-group ID below are shown as
> `<PLACEHOLDER>`. The real values live only in the operator's AWS account and
> the out-of-band runbook — never in this repo. Substitute your own when
> reproducing.

## What's deployed (live)

| Piece | Value |
|---|---|
| Distribution domain | `https://<DIST-SUBDOMAIN>.cloudfront.net` |
| Distribution ID | `E<REDACTED>` |
| Edge auth function | `anybank-finops-basic-auth` (CloudFront Functions, viewer-request) |
| Origin | `ec2-<REDACTED>.compute-1.amazonaws.com:8000` (HTTP — see origin-encryption note) |
| Origin lock | SG `sg-<REDACTED>` ingress 8000 allowed **only** from the managed prefix list `com.amazonaws.global.cloudfront.origin-facing` |
| Cache / origin-request | `Managed-CachingDisabled` / `Managed-AllViewer` (forwards the `Authorization` header) |
| Viewer protocol | redirect-to-HTTPS |

**Credentials** are NOT committed. They live only in the published CloudFront
Function code (base64) and were shared out-of-band. To see/rotate, see below.

### Origin encryption (CloudFront → origin)

**The pattern to copy — encrypt the origin hop end-to-end:**

1. Put an **Application Load Balancer** in front of the app with an **ACM
   certificate** (CloudFront requires a publicly-trusted cert on custom
   origins; ACM issues and rotates it for free):
   `aws acm request-certificate --domain-name <origin.your-domain> --validation-method DNS`
2. Point the CloudFront origin at the ALB's HTTPS listener and set the origin
   protocol policy to **`https-only`** (plus `OriginSslProtocols: TLSv1.2`).
3. Keep the SG chain locked: ALB ingress 443 from the CloudFront
   origin-facing prefix list only; app SG ingress from the ALB SG only.

That is the configuration the production target uses
(`docs/production-architecture.md`: private ALB, TLS on every hop). Anyone
reproducing this front-door for real data must implement it as above.

**This demo's accepted deviation:** the throwaway PoC origin serves the app
port directly, without an ALB, so the edge→origin hop is not TLS-terminated
at the origin. Accepted ONLY because the data is synthetic (`DEMO-*`), the
origin is reachable solely from the CloudFront prefix list, and the whole
stack is torn down after the demo window. Do not carry this deviation
anywhere real data flows.

## Architecture (why each piece)

- **Basic Auth in a CloudFront Function**, not Lambda@Edge: functions run at
  viewer-request, are cheaper, and have no cold start. The expected
  `Authorization` header is compiled into the function; reject → 401 with
  `WWW-Authenticate` so browsers show the native login prompt.
- **SG locked to the CloudFront prefix list**, not `0.0.0.0/0`: the origin
  port is reachable only from CloudFront, so nobody can bypass the edge auth
  by hitting the EC2 public IP directly.
- **CachingDisabled + AllViewer**: this is a live app with POST endpoints
  (`/ai/ask`, `/ai/canned`, `/connect/*`). AllViewer forwards the auth header
  and request body to the origin; CachingDisabled avoids serving one viewer's
  response to another. Not a CDN-optimisation use of CloudFront — it's used
  here purely as an authenticated HTTPS front door.
- **App-layer Basic Auth (defence-in-depth)**: `web/app.py` also enforces
  Basic Auth when `BASIC_AUTH_USER`/`BASIC_AUTH_PASS` are set (off by
  default). Belt-and-braces — a future SG misconfig can't leak an
  unauthenticated console. Not required while the SG lock holds.

## Reproduce / rebuild

```bash
# 1. Create + publish the auth function (edit basic-auth.js EXPECTED first)
aws cloudfront create-function --name anybank-finops-basic-auth \
  --function-config Comment="Basic Auth",Runtime=cloudfront-js-2.0 \
  --function-code fileb://deploy/cloudfront/basic-auth.js --region us-east-1
aws cloudfront publish-function --name anybank-finops-basic-auth --if-match <ETag>

# 2. Lock the origin SG to CloudFront only
aws ec2 authorize-security-group-ingress --group-id <sg-id> \
  --ip-permissions 'IpProtocol=tcp,FromPort=8000,ToPort=8000,PrefixListIds=[{PrefixListId=pl-3b927c52}]'

# 3. Create the distribution (see dist-config.json shape in git history / runbook)
```

## Rotate the password

```bash
# edit the base64 in basic-auth.js (printf 'user:newpass' | base64), then:
aws cloudfront update-function --name anybank-finops-basic-auth \
  --function-config Comment="Basic Auth",Runtime=cloudfront-js-2.0 \
  --function-code fileb://deploy/cloudfront/basic-auth.js --if-match <ETag>
aws cloudfront publish-function --name anybank-finops-basic-auth --if-match <new ETag>
# edge propagation ~seconds; no distribution change needed.
```

## Tear down (when the demo window closes)

```bash
aws cloudfront get-distribution-config --id <DIST-ID>          # note ETag
# set Enabled=false, update-distribution, wait Deployed, then:
aws cloudfront delete-distribution --id <DIST-ID> --if-match <ETag>
aws cloudfront delete-function --name anybank-finops-basic-auth --if-match <ETag>
# remove the SG rule so the origin port closes again:
aws ec2 revoke-security-group-ingress --group-id <sg-id> \
  --ip-permissions 'IpProtocol=tcp,FromPort=8000,ToPort=8000,PrefixListIds=[{PrefixListId=<cloudfront-origin-facing-pl>}]'
```

## Caveats for reviewers

- **Synthetic data only** — every figure is fake (`DEMO-*`), USD-normalised.
- **Bedrock AI is live** (us-east-1) — the free-text box makes real model
  calls. Fine for a demo; disable with `BEDROCK_DISABLED=1` if needed.
- `/healthz` is also gated by the edge function (no separate health path is
  needed; CloudFront checks the origin over the SG). If a future ALB needs an
  unauthenticated health check, exempt `/healthz` in the function.
