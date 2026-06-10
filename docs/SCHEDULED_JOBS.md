# Scheduled jobs — local debugging + production deploy

This is the operator's reference for the periodic background jobs the
backend runs. Each job is:

1. A Python script under `src/app/scripts/` that runs to completion
   and exits 0/1.
2. A Fargate task definition + EventBridge rule in CDK
   (`infra/nex_health_infra/stack.py:_build_scheduled_admin_task`).
3. Covered by integration tests under
   `tests/integration/test_scheduled_jobs.py` and the multi-layer
   harness `scripts/test_scheduled_jobs.sh`.

The contract is: any change to the scripts or their CDK wiring must
keep `scripts/test_scheduled_jobs.sh --full` passing. That single
command exercises the full path from "module imports cleanly" to "the
production CFN template contains the EventBridge rule we expect."

## Catalog

| Script | Schedule | Why | Runs as |
|---|---|---|---|
| `recompute_dashboard_rollup` | every 5 min | Rebuilds `call_metrics_daily` rollup so dashboard volume cards stay sub-millisecond as `calls` grows. Excludes today (live) so the lag is bounded to one window. | DB master role |
| `ensure_audit_partitions` | daily at 02:30 UTC | Pre-creates the next 6 monthly partitions on `audit_logs`. Without this, INSERTs whose timestamps fall outside any explicit partition land in the DEFAULT partition where queries lose pruning and INSERT throughput degrades. | DB master role |
| `cleanup_idempotency` | daily at 03:00 UTC | Prunes `retell_function_invocations`, `retell_webhook_events`, `dead_letter_events` past their retention windows (30/30/90 days). Without it these grow unbounded and INSERT performance degrades. | DB master role |
| `apply_retention_policy` | daily at 03:30 UTC | Enforces the PHI retention schedule: purges expired transcripts/summaries, deletes expired S3 recordings, clears SMS bodies, prunes notifications and dead-letter raw payloads, anonymizes contacts whose calls are all purged. Skips records under legal hold; never touches audit logs. Windows documented in `docs/compliance/policies/retention-destruction-legal-hold.md`. | DB master role |

All of these run as the database master role (`nexhealth_admin`), not the
runtime `nexhealth_app` role — they perform cross-tenant work that
must bypass row-level security. The CDK injects the same migration
secrets they would for the alembic migration task.

## Local debugging

### One-command validation

```bash
./scripts/test_scheduled_jobs.sh           # quick (~10s, layers 1–3)
./scripts/test_scheduled_jobs.sh --full    # full (~3 min, layers 1–5)
```

Use the quick form during the day-to-day dev loop. Use `--full` before
opening a PR or before any deploy that touches scheduled-job code.

### What each layer catches

The harness deliberately runs five layers of increasing fidelity. When
something breaks, you want to know which layer found it — that tells
you which class of bug you have.

**Layer 1 — module imports cleanly** (`python -c "import …"`)

Catches: syntax errors, top-level import errors, circular imports,
side-effects in module bodies that raise on load. ECS RunTask would
fail immediately on every tick if a scheduled-job module can't import.

```bash
.venv/bin/python -c "import importlib; importlib.import_module('src.app.scripts.recompute_dashboard_rollup')"
```

**Layer 2 — script standalone against your local DB** (`python -m …`)

Catches: SQL bugs, RLS context issues, wrong DSN selection, missing
environment variables, broken transaction boundaries. This is the
fastest way to actually exercise your changes against real Postgres.

```bash
set -a; source .env.local; set +a
.venv/bin/python -m src.app.scripts.recompute_dashboard_rollup
.venv/bin/python -m src.app.scripts.cleanup_idempotency
```

**Layer 3 — pytest integration suite** (`pytest tests/integration/test_scheduled_jobs.py`)

Catches: post-condition correctness, idempotency, alarm-on-failure
behaviour, drift between scripts and the dashboard's expectations of
what the rollup should contain. This is the load-bearing layer for
"is the contract still met?".

```bash
.venv/bin/python -m pytest tests/integration/test_scheduled_jobs.py -v
```

The tests seed real data, run each script as a subprocess (matching
`python -m` invocation), and assert on post-conditions:

- Rollup rows match aggregated source counts exactly.
- Re-running the script doesn't change the rollup.
- An unreachable database produces a non-zero exit code (so
  EventBridge alarms fire).
- Idempotency-table rows older than the retention window are
  pruned, recent ones are kept.

**Layer 4 — script inside the production Docker image** (`--full` only)

Catches: missing system dependencies in the image, Python version
drift, file permissions at the container layer, missing env vars the
script relies on, broken `WORKDIR`/PYTHONPATH. The image used here is
the same `Dockerfile` that ECS RunTask runs in production, so any
behaviour difference is a real bug.

```bash
docker build -t nex-health-local-validate .
docker run --rm --network host --env-file .env.local \
  nex-health-local-validate \
  python -m src.app.scripts.recompute_dashboard_rollup
```

**Layer 5 — `cdk synth` verifies wiring** (`--full` only)

Catches: cron syntax errors, missing IAM permissions on the
EventBridge → ECS RunTask path, accidental removal of a scheduled
task in a refactor, wrong subnet selection, drift between
``stack.py`` and the staged CDK output.

The harness greps the synthesised CloudFormation template for
specific resource names (`RecomputeDashboardRollupSchedule`,
`CleanupIdempotencyTaskDefinition`, etc.) and fails if any are
missing.

```bash
cd infra
PATH="$PWD/.venv/bin:$PATH" cdk synth
```

### When something fails

| Layer that broke | What it tells you | Where to look |
|---|---|---|
| 1 | A module-level import is broken. The change hasn't even been loaded yet. | The traceback output by Layer 1. Often a missing import or a dangling top-level statement. |
| 2 | The script logic is broken against a real DB. | The exception in stderr — usually SQL or RLS-context related. |
| 3 | A post-condition is violated — the script "ran" but did the wrong thing. | Read the failing assertion to see which post-condition fired (rollup totals, idempotency, exit code). |
| 4 | Layer 2 worked, but the production image has a different result. | Almost always packaging: `pyproject.toml` dep missing, `WORKDIR` wrong, env vars not forwarded. |
| 5 | The CDK stack drift — a scheduled job is missing from the synthesized CFN template. | Check `_build_scheduled_admin_task` calls and the CDK construct ids in the harness. |

## Production deploy

After Layer 5 passes:

1. `cdk deploy` from `infra/`.
2. Watch the CloudWatch log group `/{appName}/{environment}/scheduled-jobs`
   for the first three EventBridge ticks. Each invocation writes a
   stream prefixed with the job name (`rollup`, `cleanup-idempotency`).
3. Confirm `recompute_dashboard_rollup` produces a "complete:
   {'upserted': N, 'deleted': 0}" line within ~5 minutes of deploy.
4. `cleanup_idempotency` only runs at 03:00 UTC; for ad-hoc verification
   in production, invoke the task once via:

   ```bash
   aws ecs run-task \
     --cluster nex-health-<env> \
     --launch-type FARGATE \
     --task-definition <CleanupIdempotencyTaskArn from CFN outputs> \
     --network-configuration 'awsvpcConfiguration={subnets=[...],securityGroups=[...]}'
   ```

   Use the security group + subnets from the CFN outputs.

## Production observability

Each scheduled task's lifecycle events go to CloudWatch:

- `aws/events/<rule-name>` — EventBridge fired (or didn't).
- `/{appName}/{environment}/scheduled-jobs/rollup` — rollup
  recompute logs (every 5 min).
- `/{appName}/{environment}/scheduled-jobs/cleanup-idempotency` —
  cleanup logs (daily).

Suggested alarms (not yet wired in CDK — followup):

- ECS task `STOPPED` with non-zero exit code → page.
- No rollup invocation in 15 minutes → page (rule misfiring).
- Cleanup task hasn't run in 30 hours → warn (daily schedule slipped).

## Adding a new scheduled job

1. Write the script under `src/app/scripts/`. Match the shape of
   `recompute_dashboard_rollup.py` — `main()` returns 0/1, uses
   `DATABASE_ADMIN_URL`, all errors go through `logger.exception`.
2. Add an integration test to
   `tests/integration/test_scheduled_jobs.py` — at minimum: seeded
   correctness, idempotency, non-zero exit on DB failure.
3. Call `_build_scheduled_admin_task(...)` once more in
   `stack.py` with a unique `id_prefix` and a sensible
   `events.Schedule.rate(...)` or `events.Schedule.cron(...)`.
4. Add the new construct prefix to `REQUIRED_PATTERNS` in
   `scripts/test_scheduled_jobs.sh`.
5. Run `./scripts/test_scheduled_jobs.sh --full`. If green, ship it.
