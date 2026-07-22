# Production — Operations Reference

Production environment for **ScaleNexus Dashboard**, stood up 2026-06-19.

- **AWS account:** `287553036543`  ·  **Region:** `ca-central-1`
- **CloudFormation stack:** `nex-health-production`
- **Deploy as:** `AWS_PROFILE=deployer` (scoped IAM user — not root)

## Live URLs

| What | URL |
|------|-----|
| Dashboard (frontend) | https://app.scalenexus.ai |
| API | https://api.scalenexus.ai |
| API via frontend proxy | https://app.scalenexus.ai/api/... → CloudFront → ALB |
| Liveness probe | https://api.scalenexus.ai/livez |
| Readiness probe (DB + Redis) | https://api.scalenexus.ai/readyz |

> Health probes (`/livez`, `/readyz`) are at the **root**, not under `/api`. Real API routes live under `/api/...` and `/api/v1/...`.

## Observability

### CloudWatch dashboard
https://ca-central-1.console.aws.amazon.com/cloudwatch/home?region=ca-central-1#dashboards/dashboard/nex-health-production

### Log groups (console)

| Log group | Console link |
|-----------|--------------|
| API | https://ca-central-1.console.aws.amazon.com/cloudwatch/home?region=ca-central-1#logsV2:log-groups/log-group/$252Fnex-health$252Fproduction$252Fapi |
| Worker (Celery) | https://ca-central-1.console.aws.amazon.com/cloudwatch/home?region=ca-central-1#logsV2:log-groups/log-group/$252Fnex-health$252Fproduction$252Fworker |
| Migrations | https://ca-central-1.console.aws.amazon.com/cloudwatch/home?region=ca-central-1#logsV2:log-groups/log-group/$252Fnex-health$252Fproduction$252Fmigrations |
| Scheduled jobs | https://ca-central-1.console.aws.amazon.com/cloudwatch/home?region=ca-central-1#logsV2:log-groups/log-group/$252Fnex-health$252Fproduction$252Fscheduled-jobs |

### Tail logs from the terminal

```bash
aws logs tail /nex-health/production/api        --follow --profile deployer --region ca-central-1
aws logs tail /nex-health/production/worker      --follow --profile deployer --region ca-central-1
aws logs tail /nex-health/production/migrations  --since 30m --profile deployer --region ca-central-1
aws logs tail /nex-health/production/scheduled-jobs --since 1h --profile deployer --region ca-central-1
```

### Other consoles

- **ECS cluster:** https://ca-central-1.console.aws.amazon.com/ecs/v2/clusters/nex-health-production/services?region=ca-central-1
- **RDS:** https://ca-central-1.console.aws.amazon.com/rds/home?region=ca-central-1#databases:
- **Alarms (SNS topic):** `nex-health-production-AlarmTopic...` — subscribe an email in the SNS console to receive RDS/audit/5xx alarms.

## Infrastructure (right-sized for first clinic; scale up online as clients grow)

| Component | Value |
|-----------|-------|
| RDS | `db.t4g.medium`, **Multi-AZ**, PostgreSQL `16.14`, backups 14d, deletion protection ON, RDS Proxy ON |
| Redis | ElastiCache single `cache.t4g.micro` (0 replicas, no failover) |
| NAT gateways | 1 |
| API service | Fargate 1 vCPU / 2 GB, min 2 / max 10, `WEB_CONCURRENCY=2` |
| Worker service | Fargate 1 vCPU / 2 GB, min 1 / max 6 |
| DB pool (per worker) | size 10 + overflow 10 |
| API autoscaling | target CPU 50%, scale-out cooldown 60s / scale-in 180s, also request-count (800/target) |
| Password hashing | Argon2id `t=2, m=19 MiB, p=1` (OWASP tier; `p=1` is deliberate for few-vCPU tasks) |
| Recordings bucket | `nex-health-production-recordings` |

Config: `infra/config/production.json`.

## Deploy & migrate

```bash
# Build + deploy the stack (image built from working tree)
source infra/.venv/bin/activate
cd infra && cdk deploy -c config=config/production.json   # add --require-approval never for unattended

# Run DB migrations (one-off ECS task, runs `python -m src.app.scripts.migrate_database`)
AWS_PROFILE=deployer CDK_STACK_NAME=nex-health-production bash scripts/run_ecs_migration_task.sh

# Publish the frontend (build → S3 → CloudFront invalidation)
AWS_PROFILE=deployer CDK_STACK_NAME=nex-health-production bash scripts/publish_frontend_from_cdk.sh
```

> **Deferred hardening (before high-traffic onboarding):** migrate-before-traffic gating (tag-pinned ECR image) and prod-specific Makefile targets. (`minHealthyPercent: 100` and the env-namespaced RDS Proxy name are already done.) The first deploy used the safe `deploy → migrate → verify` flow because there was no live traffic yet.

## Run a one-off admin task in prod (e.g. create a super admin)

Admin scripts (super-admin invite, retention, partitions, etc.) run as the **migration task definition** with a command override. On prod they **must** use the **Migration** security group — NOT the App SG. (The `scripts/*_aws.sh` helpers default to the App SG, which only reaches the RDS Proxy; a direct-RDS admin task then times out and logs a useless empty `Failed to generate invite:` error. Staging has no proxy so its App SG works — prod differs.)

```bash
R=ca-central-1; P=deployer; STACK=nex-health-production
out() { aws cloudformation describe-stacks --stack-name $STACK --region $R --profile $P \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue|[0]" --output text; }
TD=$(out MigrationTaskDefinitionArn); MSG=$(out MigrationSecurityGroupId); SUB=$(out PrivateSubnetIds)
SUBJSON=$(echo "$SUB" | awk -F, '{for(i=1;i<=NF;i++) printf "%s\"%s\"",(i>1?",":""),$i}')

# Example: create a SUPER_ADMIN invite (emails the set-password link via Resend)
TASK=$(aws ecs run-task --cluster $STACK --launch-type FARGATE --task-definition "$TD" \
  --overrides '{"containerOverrides":[{"name":"MigrationContainer","command":[
     "python","-m","src.app.scripts.invite_super_admin","<email>","https://app.scalenexus.ai"]}]}' \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBJSON],securityGroups=[$MSG],assignPublicIp=DISABLED}" \
  --region $R --profile $P --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster $STACK --tasks "$TASK" --region $R --profile $P

# Result + one-time invite link:
aws logs get-log-events --log-group-name /nex-health/production/migrations \
  --log-stream-name "migrations/MigrationContainer/$(echo $TASK | sed 's#.*/##')" \
  --region $R --profile $P --query 'events[].message' --output text | tail -20
```

Swap the `command` array to run any admin script. The invite link is single-use and time-limited; the user also receives it by email.

## Load test results & scaling playbook

Validated on a throwaway prod-sized stack (`infra/config/loadtest.json`, proxy off, dummy third-party secrets) with k6 from a cloud EC2 (`scripts/loadtest/k6_ceiling.js`). Two endpoints: `/readyz` (DB+Redis ping → raw throughput) and `POST /api/auth/login` (Argon2id → CPU/auth ceiling).

| Config | Read ceiling | Login ceiling | Notes |
|---|---|---|---|
| **Original lean** (1 vCPU, `WEB_CONCURRENCY=2`, pool 5, Argon2 `p=4`) | ~100 rps (p95 → 10–26s past it) | ~10–15/s, 46% success, p95 ~55s | bottleneck = API concurrency + Argon2 thread contention; **DB only 18% CPU** |
| **Tuned** (2 vCPU, `WEB_CONCURRENCY=4`, pool 10, Argon2 `p=1`) | **>2,000 rps** at p95 **3ms** | **>150/s, 100% success**, p95 **255ms** | ramp maxed without breaking — true ceiling is higher |

**Current prod runs the lean per-task size (1 vCPU) but keeps every free win** (Argon2 `p=1`, pool 10, autoscaling 50%, maxCount 10) — overwhelming headroom for one clinic. The single most important fix was **Argon2id `parallelism 4→1`**: on 1–2 vCPU tasks `p=4` thrashed one core; `p=1` made logins ~5–10× faster and is vCPU-independent.

**Knobs to turn as you onboard more clinics (all online / no downtime — see verification in git history):**
1. `api.cpu` 1024 → 2048 (+ `memoryMiB` 4096) and `api.webConcurrency` 2 → 4 — the validated config that hit >2,000 rps / >150 logins/s.
2. `api.minCount` ↑ for baseline headroom; `api.maxCount` already 10.
3. The **DB is the last thing to scale** — it sat at 18% CPU when the app saturated. Bump `database.instanceType` (online resize) only if RDS CPU/connections actually climb.

> Bottleneck order observed: **API CPU/concurrency → Argon2 (login) → (DB has huge headroom).** Scaling is almost entirely config in `production.json`; no application rewrite required.
