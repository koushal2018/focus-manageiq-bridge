# CloudFront share — AnyBank FinOps PoC (synthetic)

A CloudFront distribution fronts the EC2-hosted console so it can be shared
with the client and internal peer reviewers over **HTTPS with HTTP Basic Auth
at the edge**. The data is synthetic (`DEMO-*`); the auth gate keeps a
bank-branded demo off the open internet.

## What's deployed (live)

| Piece | Value |
|---|---|
| Distribution domain | `https://dk98mfrqqplu7.cloudfront.net` |
| Distribution ID | `E2CJX2SLDSFI3Z` |
| Edge auth function | `anybank-finops-basic-auth` (CloudFront Functions, viewer-request) |
| Origin | `ec2-32-195-142-246.compute-1.amazonaws.com:8000` (HTTP) |
| Origin lock | SG `sg-03e1eecf3082d6208` ingress 8000 allowed **only** from prefix list `pl-3b927c52` (`com.amazonaws.global.cloudfront.origin-facing`) |
| Cache / origin-request | `Managed-CachingDisabled` / `Managed-AllViewer` (forwards the `Authorization` header) |
| Viewer protocol | redirect-to-HTTPS |

**Credentials** are NOT committed. They live only in the published CloudFront
Function code (base64) and were shared out-of-band. To see/rotate, see below.

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
aws cloudfront get-distribution-config --id E2CJX2SLDSFI3Z          # note ETag
# set Enabled=false, update-distribution, wait Deployed, then:
aws cloudfront delete-distribution --id E2CJX2SLDSFI3Z --if-match <ETag>
aws cloudfront delete-function --name anybank-finops-basic-auth --if-match <ETag>
# remove the SG rule so the origin port closes again:
aws ec2 revoke-security-group-ingress --group-id sg-03e1eecf3082d6208 \
  --ip-permissions 'IpProtocol=tcp,FromPort=8000,ToPort=8000,PrefixListIds=[{PrefixListId=pl-3b927c52}]'
```

## Caveats for reviewers

- **Synthetic data only** — every figure is fake (`DEMO-*`), USD-normalised.
- **Bedrock AI is live** (us-east-1) — the free-text box makes real model
  calls. Fine for a demo; disable with `BEDROCK_DISABLED=1` if needed.
- `/healthz` is also gated by the edge function (no separate health path is
  needed; CloudFront checks the origin over the SG). If a future ALB needs an
  unauthenticated health check, exempt `/healthz` in the function.
