# Staging Domain Setup — `sn.dev.staging.scalenexus.ai`

This document captures the one-time setup to wire the staging
environment to a real domain so that:

- CloudFront serves `sn.dev.staging.scalenexus.ai` with a public TLS cert.
- ALB serves `api.sn.dev.staging.scalenexus.ai` with its own public TLS cert.
- The end-to-end path becomes HIPAA-compliant for ePHI transport
  (HIPAA §164.312(e)).

The parent domain `scalenexus.ai` is registered with the client at
GoDaddy. We do not have access to that GoDaddy account, and we don't
need it after the one delegation step below.

## Architecture summary

```
client → CloudFront (cert in us-east-1)        → frontend bucket (S3)
       (custom domain: sn.dev.staging.scalenexus.ai)
                     │
                     └─► /api/*  → ALB (cert in ca-central-1)  → ECS Fargate API
                                  (custom domain: api.sn.dev.staging.scalenexus.ai)
```

A Route 53 public hosted zone for `staging.scalenexus.ai` lives in
**our** AWS account (`287553036543`). The client adds 4 NS records on
GoDaddy once, delegating only that subdomain to us. From then on, all
DNS records under `staging.scalenexus.ai` — including the deeper
hostnames this app uses (`sn.dev.staging.scalenexus.ai`,
`api.sn.dev.staging.scalenexus.ai`), ACM cert validation CNAMEs, and
any future subdomains — are managed by us. The client's GoDaddy
account is never touched again.

## Step 1 — Hosted zone (DONE)

The hosted zone exists in our account:

| Field | Value |
|---|---|
| Account | `287553036543` |
| Zone ID | `Z00167603B8UEPDURH4XN` |
| Zone name | `staging.scalenexus.ai` |
| Type | Public |

The zone is empty apart from the SOA + NS records AWS auto-creates.
Until the client adds the NS delegation in step 2, queries for
anything under `staging.scalenexus.ai` from the public internet will
return NXDOMAIN.

## Step 2 — Delegation to send the client

Send the client the following message + records (paste verbatim).
**Replace nothing — these are the actual NS values for our zone.**

> Hi — we're deploying our staging environment at
> `sn.dev.staging.scalenexus.ai`. Could you add these four NS records
> on GoDaddy on the `scalenexus.ai` zone? They delegate only the
> `staging` subdomain to AWS Route 53; `scalenexus.ai` itself and any
> other records you have stay untouched. The four records cover every
> hostname under `staging.scalenexus.ai` — we'll carve out
> `sn.dev.staging.scalenexus.ai` and its API counterpart from our side
> after delegation propagates, no further DNS changes from you.
>
> ```
> Type: NS   Host: staging   Value: ns-1371.awsdns-43.org
> Type: NS   Host: staging   Value: ns-501.awsdns-62.com
> Type: NS   Host: staging   Value: ns-732.awsdns-27.net
> Type: NS   Host: staging   Value: ns-1541.awsdns-00.co.uk
> ```
>
> TTL: GoDaddy default (1 hour) is fine. After it propagates (~5–60
> min) we'll provision certs and complete the staging deploy. You
> won't need to touch DNS again — once the subdomain is delegated,
> we manage everything under it from our AWS account.

GoDaddy's UI for this:

1. Domain Portfolio → click `scalenexus.ai`.
2. **DNS** in the left sidebar (some accounts: **Manage DNS**).
3. Scroll past the Nameservers section. We do NOT want to change
   the zone's nameservers — that would un-delegate the entire
   domain from GoDaddy.
4. In the **DNS Records** table, click **Add New Record** (or **+**).
5. Type: **NS** · Name/Host: `staging` · Value: one of the four
   ``ns-*.awsdns-*`` values · TTL: 1 hour default.
6. Repeat for the other three.
7. Save. Result: four new rows alongside the existing A / MX / TXT
   records. The zone's overall nameservers stay untouched.

GoDaddy reference: <https://www.godaddy.com/help/manage-dns-records-680>

### When the client can't add NS records (Option B fallback)

Some GoDaddy account types (locked-down enterprise plans, etc.)
restrict NS-record creation in the DNS-record editor. In that case
skip the delegation entirely and have the client add CNAMEs
directly:

1. ACM cert validation CNAME for `sn.dev.staging.scalenexus.ai`
   (us-east-1 cert). Must persist forever — ACM rechecks at every
   60-day renewal.
2. ACM cert validation CNAME for
   `api.sn.dev.staging.scalenexus.ai` (ca-central-1 cert). Same
   persistence requirement.
3. After ``cdk deploy``: CNAME `sn.dev.staging` → CloudFront DNS
   from CFN output.
4. CNAME `api.sn.dev.staging` → ALB DNS from CFN output.

Trade-off: the client touches GoDaddy every time we add a
subdomain. Operationally noisier than Option A, same final
behaviour.

## Step 3 — Verify delegation propagated

Once the client confirms, query the parent zone — only it knows
whether the NS delegation has been put in place:

```bash
dig +short NS staging.scalenexus.ai
```

When you see all four `ns-*.awsdns-*` records returned (in any order),
delegation is live. Until then, you'll get an empty response.

You can also confirm with Google's DNS:

```bash
dig +short NS staging.scalenexus.ai @8.8.8.8
```

## Step 4 — Request ACM certs

Two certs are needed (different regions because CloudFront and ALB
use different ones):

```bash
# Cert for CloudFront — must be in us-east-1
aws acm request-certificate \
  --region us-east-1 \
  --domain-name sn.dev.staging.scalenexus.ai \
  --validation-method DNS \
  --query 'CertificateArn' --output text
# → arn:aws:acm:us-east-1:287553036543:certificate/<id>

# Cert for ALB — must be in the stack's region (ca-central-1)
aws acm request-certificate \
  --region ca-central-1 \
  --domain-name api.sn.dev.staging.scalenexus.ai \
  --validation-method DNS \
  --query 'CertificateArn' --output text
# → arn:aws:acm:ca-central-1:287553036543:certificate/<id>
```

Each `request-certificate` returns an ARN. Hold onto both.

## Step 5 — Add the ACM validation CNAMEs to our zone

ACM emits one validation CNAME per cert. Fetch them:

```bash
aws acm describe-certificate --region us-east-1 \
  --certificate-arn <us-east-1 arn> \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord'

aws acm describe-certificate --region ca-central-1 \
  --certificate-arn <ca-central-1 arn> \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord'
```

Each returns `{Name, Type, Value}`. Add them to **our** Route 53
hosted zone (`Z00167603B8UEPDURH4XN`):

```bash
cat <<JSON > /tmp/cf-acm-validation.json
{
  "Changes": [{
    "Action": "UPSERT",
    "ResourceRecordSet": {
      "Name": "<Name from describe-certificate>",
      "Type": "CNAME",
      "TTL": 300,
      "ResourceRecords": [{"Value": "<Value from describe-certificate>"}]
    }
  }]
}
JSON

aws route53 change-resource-record-sets \
  --hosted-zone-id Z00167603B8UEPDURH4XN \
  --change-batch file:///tmp/cf-acm-validation.json
```

(Same shape for the ALB cert validation record.)

ACM auto-detects validation usually within 5–10 minutes. Confirm:

```bash
aws acm describe-certificate --region us-east-1 \
  --certificate-arn <arn> --query 'Certificate.Status'
# → "ISSUED"
```

## Step 6 — Update `infra/config/staging.json`

Add the domain config to both `frontend` and `api` blocks:

```json
{
  "appName": "nex-health",
  "environmentName": "staging",
  "account": "287553036543",
  "region": "ca-central-1",
  ...
  "corsAllowedOrigins": [
    "https://sn.dev.staging.scalenexus.ai"
  ],
  "authFrontendBaseUrl": "https://sn.dev.staging.scalenexus.ai",
  ...
  "api": {
    "cpu": 512,
    "memoryMiB": 1024,
    "desiredCount": 1,
    "minCount": 1,
    "maxCount": 2,
    "containerPort": 8000,
    "domainName": "api.sn.dev.staging.scalenexus.ai",
    "certificateArn": "arn:aws:acm:ca-central-1:287553036543:certificate/<id>",
    "hostedZoneName": "staging.scalenexus.ai"
  },
  ...
  "frontend": {
    "enabled": true,
    "domainName": "sn.dev.staging.scalenexus.ai",
    "certificateArn": "arn:aws:acm:us-east-1:287553036543:certificate/<id>",
    "hostedZoneName": "staging.scalenexus.ai"
  }
}
```

Replace `<id>` with the actual cert IDs from step 4. Also update
`corsAllowedOrigins` and `authFrontendBaseUrl` to the new URL — both
are read by the FastAPI app at boot.

## Step 7 — Deploy

```bash
cd infra
PATH="$PWD/.venv/bin:$PATH" cdk deploy
```

This will:
- Attach the ALB cert to a 443 HTTPS listener (port 80 redirects to 443).
- Attach the CloudFront cert as an alternate domain on the distribution.
- Create A-alias records `sn.dev.staging.scalenexus.ai → CloudFront` and
  `api.sn.dev.staging.scalenexus.ai → ALB` automatically (Route 53 alias).
- Flip CloudFront origin protocol to HTTPS_ONLY (the gate from issue
  #1 — `_build_api_service` now provisions HTTPS, the origin matches).
- Stop emitting the *"NOT HIPAA-compliant"* annotation warning at
  synth time.

## Step 8 — Verify end-to-end

```bash
# Frontend
curl -sI https://sn.dev.staging.scalenexus.ai | head -5

# API health
curl -sI https://sn.dev.staging.scalenexus.ai/api/health
curl -sI https://api.sn.dev.staging.scalenexus.ai/livez

# Cert chain — should be Amazon-issued, valid, SAN matches
openssl s_client -connect sn.dev.staging.scalenexus.ai:443 -servername sn.dev.staging.scalenexus.ai </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -dates
```

CloudFront and ALB should both serve valid Amazon-issued certs and
redirect HTTP → HTTPS at the edge.

## Future subdomains

The hosted zone is yours. Any new record anywhere under
`staging.scalenexus.ai` — for example a different app at
`other-app.dev.staging.scalenexus.ai`, or `monitoring.staging.scalenexus.ai`
— is just another `change-resource-record-sets` call. No client
involvement, ever, after the one-time NS delegation.

## Cost

| Resource | Monthly |
|---|---|
| Route 53 hosted zone | ~$0.50 |
| Route 53 queries (staging traffic) | ~$0.40 / million |
| ACM cert | $0 (free for AWS-managed certs in ALB/CloudFront) |
| **Total marginal** | **~$0.50–$1 / month** |

## Quick reference

| Item | Value |
|---|---|
| AWS account | `287553036543` |
| Hosted zone ID | `Z00167603B8UEPDURH4XN` |
| Zone name | `staging.scalenexus.ai` |
| Frontend cert region | `us-east-1` |
| ALB cert region | `ca-central-1` |
| Frontend hostname | `sn.dev.staging.scalenexus.ai` |
| API hostname | `api.sn.dev.staging.scalenexus.ai` |
