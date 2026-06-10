# Platform Architecture

Last reviewed: June 2026, against branch `staging-scalenexus-rename`.

This is the system-level overview: what the components are, how a request flows
through them, and where the important invariants live. Deep-dives are split out:

- [NEXHEALTH.md](NEXHEALTH.md) — the PMS integration, including its caveats and edge cases
- [SECURITY.md](SECURITY.md) — auth, MFA, tenant isolation, PHI encryption, retention
- [DEPLOYMENT_AND_HIPAA_GUIDE.md](DEPLOYMENT_AND_HIPAA_GUIDE.md) — deploy runbook and infra compliance
- [SCHEDULED_JOBS.md](SCHEDULED_JOBS.md) — cron jobs and how to debug them

## What the platform does

Dental/medical clinics get an AI voice agent (built on Retell) that answers their
phone, looks up patients, and books/cancels/reschedules appointments directly in
their practice-management system (NexHealth). Clinic staff get a web dashboard
showing every call with a transcript, summary, tags (new patient, emergency,
needs-callback, …), a callback queue, and daily metrics. The platform notifies
staff about calls that need attention via email, in-app notifications, and SMS.

Tenancy is per clinic: a clinic company is an *institution* with N physical
*locations*, and every PHI-bearing row is scoped to one. The product ships
under the ScaleNexus brand.

## System context

```
  caller ──phone──> Retell (voice agent, LLM)
                       │ function calls + post-call webhooks (HMAC-signed)
                       v
  clinic staff ──> CloudFront/S3 ──> ALB ──> FastAPI API (ECS Fargate)
  (dashboard)        (React SPA)              │        │
                                              │        ├──> PostgreSQL (RDS) — RLS-enforced multi-tenant
                                              │        ├──> Redis (ElastiCache) — sessions, rate limits,
                                              │        │      NexHealth token cache, SSE pub/sub
                                              │        └──> NexHealth API (patients, slots, bookings)
                                              v
                                   Celery worker (ECS Fargate)
                                     ├──> Resend (email)
                                     ├──> Twilio (SMS)
                                     └──> S3 (call recordings)
```

Everything PHI-bearing (RDS, Redis, ECS tasks) sits in private subnets; only the
ALB and CloudFront are public. Region is `ca-central-1` (Canadian data residency).

## Components

| Component | Code | Runs as |
|---|---|---|
| API | `src/app/main.py` (FastAPI, async SQLAlchemy) | Gunicorn + UvicornWorker, ECS service |
| Worker | `src/app/worker.py` (Celery, Redis broker) | ECS service, queues: `notifications_default`, `notifications_high`, `webhooks` |
| Dashboard | `nexus-dashboard-web/` (Vite + React 19 + TS) | Static, S3 + CloudFront |
| Migrations | `alembic/` via `src/app/scripts/migrate_database.py` | One-off ECS task, runs before app deploy |
| Scheduled jobs | `src/app/scripts/` | EventBridge → ECS RunTask (see SCHEDULED_JOBS.md) |
| Infra | `infra/` (CDK, Python) | `make cdk-deploy-staging` etc. |

## Request lifecycle (API)

Middleware, in execution order (registration is reversed — Starlette runs
last-registered first; there's a comment about this footgun in `main.py`):

1. `RequestIDMiddleware` — X-Request-ID in, bound to structlog context.
2. `SlowAPIMiddleware` — per-IP rate limiting, Redis-backed. Runs *before* any
   DB work on purpose: an unauthenticated flood with random tenant headers must
   not be able to trigger per-request DB lookups (`src/app/api/rate_limit.py`).
   Client IP is taken from X-Forwarded-For only when the peer is inside
   `TRUSTED_PROXY_CIDRS`.
3. `InstitutionMiddleware` — resolves `X-Institution-Slug` / `X-Location-Slug`
   to DB rows on `request.state` (`src/app/middleware/institution.py`).
4. `SecurityHeadersMiddleware` — HSTS, `Cache-Control: no-store` (PHI), frame
   denial, permissions policy.
5. CORS.

Authentication is a FastAPI dependency (`src/app/api/deps.py`): Bearer JWT →
decode → check the JTI hasn't been revoked (Redis) → check MFA satisfied for the
role → load user. The dependency also sets the **RLS context** for the request —
see the next section, this is the most important invariant in the codebase.

Routers are mounted in `main.py` (~20 of them under `/api`): auth, admin
institution CRUD, institution portal, calls, dashboard, callbacks, notifications,
SMS, dead-letter replay, SSE, plus the Retell function/webhook endpoints under
`/api/v1`. The route files under `src/app/api/routes/` map 1:1 to those names.

## Multi-tenancy

Tenant root is `Institution` (one clinic company); each has N
`InstitutionLocation`s (physical practices). Users, contacts, calls, SMS logs,
notifications — every PHI-bearing table — carry `institution_id`.

Isolation is enforced at two layers:

- **Application**: every query path goes through scoped dependencies that
  resolve the caller's institution/location and reject cross-tenant IDs
  (`src/app/api/deps_scope.py`, `src/app/pms/factory.py`).
- **Postgres RLS**: the baseline migration
  (`alembic/versions/20260510_consolidated_baseline.py`) creates
  `CREATE POLICY … USING (institution_id = current_setting('app.institution_id')::uuid)`
  on every tenant-scoped table, and the runtime DB role is created with
  `NOBYPASSRLS`. The per-request context is applied with one `set_config()`
  round-trip per transaction; `RlsAsyncSession` re-applies it after
  commit/rollback because the pool can hand back a different connection
  (`src/app/database.py`).

Two guards keep this honest:

- On startup the app queries `pg_class` for any table that has an
  `institution_id` column but `relrowsecurity = false` and logs CRITICAL
  (`src/app/main.py`). A new table that forgets RLS shows up on the next deploy.
- An `rls`-marked pytest tier runs the policies against a real Postgres
  (testcontainers) — see Testing below.

Cross-tenant work (dashboard rollups, retention purges, migrations) runs under
the admin role via `DATABASE_ADMIN_URL`, never through the app role.

## Call lifecycle (the core product loop)

**During the call** — Retell's LLM invokes our functions over
`POST /api/v1/retell/functions` (HMAC-verified, `src/app/retell/security.py`).
The agent ID maps 1:1 to an `InstitutionLocation`, which scopes everything else.
Registered functions (`src/app/retell/handlers.py`):

- Read-only: `list_locations`, `get_location_details`, `lookup_patient`,
  `find_appointment_slots`, `list_appointment_types`, `list_providers`,
  `list_insurance_plans`, `list_transfer_numbers`, `list_operatories`
- Mutating, wrapped in idempotency: `create_patient`, `book_appointment`,
  `cancel_appointment`, `reschedule_appointment`

Idempotency (`src/app/retell/idempotency.py`): unique on
`(call_id, function_name, HMAC(args))` in `retell_function_invocations`.
Retell retries a function call → we replay the cached result instead of
double-booking. In-flight duplicates get a retryable "still processing" response.

`lookup_patient` sits behind an identity gate: the caller must provide DOB plus
either exact email or phone-last-4 before any PHI is read back.

**After the call** — Retell posts a `call_analyzed` webhook
(`src/app/retell/webhooks.py`). The request thread only verifies the signature,
claims an idempotency row (`retell_webhook_events`, unique on
`(call_id, event_type)`), and enqueues a Celery task — response in <100 ms.
The worker then (`src/app/services/post_call_service.py`):

1. Resolves institution/location from the agent ID.
2. Upserts a `Contact` (matched by phone hash) and writes the `Call` row —
   transcript and summary encrypted, and only Retell's *scrubbed* outputs are
   ever persisted.
3. Fans out: recording download → S3 (`tasks/recordings.py`), staff email via
   Resend, in-app notifications per recipient, auto-SMS to the patient if the
   agent composed one (consent-gated, see SECURITY.md).
4. Publishes an SSE hint over Redis pub/sub (`src/app/services/event_bus.py`) so
   open dashboards refetch. Events are deliberately payload-free
   (`calls_updated`, etc.) — the SSE channel never carries PHI, clients refetch
   through the authenticated API.

Anything that fails against a vendor lands in `dead_letter_events` with a
redacted payload (always) and an encrypted raw payload (purged after 30 days),
replayable from the admin UI (`src/app/services/dead_letter.py`).

## Data model

28 tables; the ones worth knowing (all under `src/app/models/`):

- `institutions`, `institution_locations` — tenancy. Per-location NexHealth
  binding (`nexhealth_subdomain`, `nexhealth_location_id`) and Retell binding
  (`retell_agent_id`). Locations also own operating hours, breaks, and transfer
  numbers (their own tables).
- `institution_providers` / `_operatories` / `_appointment_types` /
  `_descriptors` — local cache of PMS reference data, synced on demand
  (`src/app/services/sync_service.py`).
- `contacts` — callers/patients. Email/phone/DOB encrypted; `phone_hash` for
  caller-ID lookup; `nexhealth_patient_id` links to the PMS; `anonymized_at`
  set when retention strips identity.
- `calls` — one per analyzed call. Encrypted transcript/summary, status + tags,
  retention columns (`retain_until`, `recording_retain_until`, `legal_hold_until`,
  `purged_at`).
- `call_metrics_daily` — pre-aggregated dashboard rollup, recomputed every
  5 minutes (it's the only way dashboard reads stay cheap past ~100k calls).
- `users` + MFA tables (`webauthn_credentials`, `user_totp_factors`,
  `user_recovery_codes`) — see SECURITY.md.
- `audit_logs` — append-only (DB trigger blocks UPDATE/DELETE, plus TRUNCATE
  protection), range-partitioned by month, partitions pre-created by a daily job.
- `sms_history_logs`, `sms_consents` (+ suppression/DNC) — see SECURITY.md.
- `retell_webhook_events`, `retell_function_invocations`, `dead_letter_events` —
  idempotency and failure capture described above.

Conventions: UUID PKs everywhere; PHI columns encrypted at the application layer
(AES-256-GCM, key derived from `ENCRYPTION_KEY`); soft-delete for users
(partial unique index on email `WHERE deleted_at IS NULL`), anonymization for
contacts, purge-in-place for calls.

## Background work

Celery on Redis, JSON serializer only, `acks_late` with prefetch 1. Webhook
processing gets a dedicated queue so a burst of notification emails can't starve
call ingestion. Workers use `NullPool` for asyncpg — each task gets a fresh
connection because event loops are task-local after fork (`src/app/worker.py`).

Recurring jobs run as EventBridge-triggered ECS tasks, not Celery beat:
dashboard rollup (5 min), audit partition pre-creation (daily), idempotency/
dead-letter pruning (daily). SCHEDULED_JOBS.md has the catalog and a 5-layer
local test harness.

## Frontend

Vite + React 19 + TypeScript, React Router v7 with role-gated routes
(`nexus-dashboard-web/src/router.tsx`). Auth: access token in memory, refresh
token in an HttpOnly cookie, axios interceptor refresh, 15-minute inactivity
logout. MFA enrollment/step-up UI on `/security` (TOTP via qrcode.react,
passkeys via @simplewebauthn/browser). Live updates via SSE.

Branding (title, logo, API target) is fixed at build time via `index.html`,
static assets, and `VITE_*` env vars.

## Testing

Three tiers (`pytest.ini` markers):

- unit — mocked sessions, no infra needed; the bulk of the suite.
- `integration` — real Postgres via testcontainers.
- `rls` — verifies row-level-security policies against a real DB; skipped by
  default (needs Docker + `TESTCONTAINERS_RYUK_DISABLED=true` on some setups),
  run manually before schema changes touching tenancy.

`make test`, `make lint` (ruff). Frontend: Vitest + testing-library.

## In-flight work, roadmap, known limitations

- **No-PMS mode** (unmerged, `feat/native-pms-mode` branch): clinics without
  NexHealth get the call-intelligence side only — Retell webhook ingest,
  transcripts, summaries, tags, callback queue — with the booking and
  availability functions disabled. On this branch the factory only builds the
  NexHealth adapter.
- **Single NexHealth account**: all clinics share one platform API key;
  isolation comes from per-location subdomain + location ID (fail-closed if
  either is missing). `institutions.nexhealth_api_key_encrypted` exists for a
  future per-clinic-credentials model but is not wired into the adapter path.
- **CI/CD** is manual (`make cdk-deploy-staging` + migration task + frontend
  publish). No GitHub Actions yet; production config is deliberately not
  automated.
Near-term roadmap, roughly in order:

1. Frontend overhaul — the dashboard UI is the next major work item.
2. Production launch hardening: HTTPS listener on the ALB, production CDK
   config, and the security backlog (self-serve account unlock for
   institution admins, `TWILIO_*` env rename, tag-filter normalization,
   phone search via `phone_hash`).
3. Audit-log archival job — partitions are already monthly, so this is
   export-to-S3 + drop-partition on a 6-year window.
4. SMS quiet hours.
5. CI/CD: lint + unit tier on PR, then staged deploys.
6. No-PMS mode (merge of `feat/native-pms-mode`): call intelligence without
   scheduling for clinics that don't use NexHealth.
7. Evaluate NexHealth's new-generation (beta) API — better naming and
   self-serve working-window configuration; see the "Stable vs. new API"
   section in NEXHEALTH.md. Exploration only, no migration scheduled.
