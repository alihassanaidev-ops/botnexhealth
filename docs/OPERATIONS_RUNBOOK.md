# Operations Runbook (lean)

**Scope:** the ScaleNexus platform (`botnexhealth`). For day-to-day operation and
incident response. Deep architecture lives in `REPOSITORY_CONTEXT.md`.

## 1. Environments

| | Staging | Production |
|---|---|---|
| AWS account | `287553036543` (ca-central-1) | `287553036543` (ca-central-1) |
| CDK stack | `nex-health-staging` | `nex-health-production` |
| API | `https://api.staging.scalenexus.ai` | `https://api.scalenexus.ai` |
| Dashboard | `https://staging.scalenexus.ai` | `https://scalenexus.ai` |
| Log groups | `/nex-health/staging/{api,worker,migrations,scheduled-jobs}` | `/nex-health/production/{...}` |

Both live in the **same** AWS account; only the stack name separates them. Always
pass the config file explicitly so you never touch the wrong one.

## 2. Deploy

```bash
cd infra && source .venv/bin/activate
cdk diff  -c config=config/staging.json      # always diff first
cdk deploy -c config=config/staging.json     # --require-approval never to skip the prompt
```

Then, only if migrations changed:
```bash
CDK_STACK_NAME=nex-health-staging bash scripts/run_ecs_migration_task.sh
```
and for a frontend change: `bash scripts/publish_frontend_from_cdk.sh`.

**Rules:** staging first, always `cdk diff` before `cdk deploy`, never deploy
production without a second pair of eyes. A bad deploy rolls back by re-pointing
the ECS service to the previous task definition; the previous definitions are
retained.

## 3. Health checks

- `curl -s https://<api-host>/livez` → `{"status":"alive"}`.
- ECS: `aws ecs describe-services --cluster nex-health-<env> --services ...`
  (desired == running, rolloutState COMPLETED).
- ALB target health: `aws elbv2 describe-target-health --target-group-arn <arn>`.
- CloudWatch dashboard: `nex-health-<env>`; alarms publish to the stack's
  `AlarmTopicArn`.

## 4. Common situations

**API 5xx / high latency.** Check `/nex-health/<env>/api` for tracebacks
(`filter-log-events ... | grep ERROR`). A `RLS NOT ENABLED on tenant-scoped
tables` line is a startup CRITICAL — a migration is missing; see §6.

**Stuck queues / dead letters.** Dead-letter events are listed under Admin →
Undeliverable or via `GET /api/institution/undeliverables`. Replay or discard
through the UI; both actions are audited.

**NexHealth webhooks not arriving.** Admin → NexHealth webhook controls can
re-subscribe; verify the subscription secret is current. Failed deliveries land
in the dead-letter queue.

**GoTracker connector offline (Olive Tree).** The connector runs next to the
clinic's SQL Server. If bookings stop syncing, check the Cloud Service operator
health panel for the connector's last-seen time, then the clinic's PC/power and
the connector service. Operator alerts fire on the Cloud Service side.

**Circuit breaker open.** Outbound SMS/voice/email stops when a provider is
failing. It resets on its own once the provider recovers; check the provider's
status before overriding.

## 5. Backups and restore

- **RDS PostgreSQL:** automated snapshots (retention per stack config) plus
  point-in-time recovery. Restore by creating a new instance from a snapshot,
  then repointing `DATABASE_HOST` (a rehearsal script exists under
  `scripts/prod_db_rehearsal/`).
- **S3:** recordings and the frontend bucket are versioned; lifecycle rules
  expire recordings at the retention window.
- **Secrets:** AWS Secrets Manager (no manual backup; use rotation).
- **Restore order:** database → verify migrations at head → API/worker → frontend.

## 6. Migrations and RLS

- Migrations run as a one-off ECS task, never on API startup.
- The app logs **CRITICAL** if a table with `institution_id` has RLS off. Fix by
  adding a migration that enables + forces RLS with a tenant policy (see
  `alembic/versions/20261015_internal_status_events_rls.py` for the pattern).
- The integration tier (`make test-rls`) builds a real Postgres, runs the full
  chain, and fails if any tenant table is unprotected.

## 7. Disaster recovery (summary)

If the platform is down mid-campaign: scheduled steps and timers are stored in
Postgres, so they resume when the worker/scheduler return — nothing is lost, but
in-flight calls/texts already sent are not re-sent (idempotency keys). Recovery
is restore-from-snapshot (§5). A full DR drill in staging is still outstanding.

## 8. SMS / Twilio

Five Canadian **local** numbers, all SMS-capable and `in-use`, shared by staging
and production. There is **no** US 10DLC/A2P registration and **no** messaging
service or regulatory bundle configured — Canadian local long-code SMS does not
use 10DLC. Nothing is currently pending on the carrier side. Re-check with:

```bash
curl -s -u "$SID:$TOKEN" \
  "https://api.twilio.com/2010-04-01/Accounts/$SID/IncomingPhoneNumbers.json"
```
