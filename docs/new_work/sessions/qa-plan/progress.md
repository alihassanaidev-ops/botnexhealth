# Progress: QA execution

## 2026-07-13 — Layer 0 + Layer 1 executed

### Layer 0 — Environment
- 0.1 ✅ `make up-deps` — Postgres :5433 + Redis :6379 up (docker compose).
- 0.2 ✅ Fresh migration to head — **found + fixed a real bug**: the SMS migration's revision id
  `20260712_sms_workflow_attribution` was **33 chars > alembic_version varchar(32)**. Never surfaced
  before because it was only offline-checked (`alembic heads`) + unit-tested, never *applied*. First
  real apply failed on the version stamp. Shortened to `20260712_sms_wf_attribution` (27), renamed the
  file, re-applied clean. **This is a fix to an already-committed migration → needs a follow-up commit.**
- 0.3 ✅ `alembic current` == `heads` == `20260712_sms_wf_attribution` (single head).
- 0.4 ☐ FE `node_modules` ownership — **BLOCKED, needs `sudo chown` (user).**
- 0.5 ☐ Vendor sandbox creds — user/ops.
- 0.6 ☐ Seed tenant — pending (needed for Layer 2).

### Layer 1 — Automated regression (backend GREEN)
- 1.1 ✅ Backend unit: **1482 passed, 0 failed** (with DB+Redis up, the 3 previously-Redis-down
  appointment tests now pass).
- 1.1b ✅ Installed missing `respx` dev dep → the 2 previously collection-broken files
  (`test_locations_routes`, `test_nexhealth_client`) now pass (7). **Full unit suite is now green with
  zero exclusions.**
- 1.2 ✅ Integration (real Postgres, testcontainers): **12/12 passed** — migration chain + engine +
  voice on a fresh DB (5m26s).
- 1.3 ✅ RLS / tenant-scope invariant + RBAC matrix green (part of the 1482).
- 1.4 ✅ Frontend (after user ran `chown` + `npm install`, 484 pkgs): **tsc clean (exit 0)** —
  DNC UI + Plan 08 fix + builder all typecheck. **vitest: 140 passed / 23 files** (with
  `--testTimeout=20000`). My changes verified: `do-not-contact-api` + `automation-api` 10/10.
  NOTE: default 5s timeout causes cold-run flakiness on 4 WorkflowBuilder.publish tests under parallel
  load (all pass in isolation / with a higher timeout) → tiny follow-up: bump `testTimeout` in vitest config.

### Net
Backend QA baseline is fully green. Remaining to start Layer 2/3 is user/ops-dependent:
`sudo chown node_modules`, vendor sandbox creds, seed a tenant. Then drive the Layer 2 E2E flows.

### Layer 2 — Local E2E backend (durable path chosen: real-Postgres integration tests)
Chose Option B (extend the integration suite) over fragile hand-driven curl E2E — the engine core
was already covered by the 12 integration tests; the gap was the channel/webhook/gate paths (mock-only).
- ✅ NEW `tests/integration/test_outbound_channels_integration.py` — **4 passed** (real Postgres,
  vendors stubbed at the HTTP boundary), independently re-run confirmed:
  1. SMS send E2E → `sms_history_logs` carries `workflow_run_id` + `workflow_id` (Fix 2, Plan 04/11).
  2. Compliance gate blocks on DNC → run FAILED `compliance_blocked`, no send attempted (Plan 12).
  3. Email suppression by `email_hash` → REVOKED EMAIL consent → gate blocks subsequent email (Fix 1, Plan 05).
  4. Voice metadata carries `workflow_id` (Fix 2, Plan 03/11).
- **No bugs found** — all three shipped fixes behave correctly against real Postgres.
- Note: test 3 exercises the service method (`record_email_consent_identity`) not the Celery `.delay`
  fan-out wrapper (which opens its own global-engine session; covered by unit tests).

### Layer 2 verdict
Backend E2E for the engine (12 tests) + channels/gate/fixes (4 tests) is now durable + green.
Remaining Layer 2 breadth (more channels/edge cases) can grow the same file over time.

### Live API smoke (bridge check — 2026-07-14)
Brought up the API container (`docker compose up -d api`); `/livez` → 200 after 18s.
`POST /api/auth/login` (inst.admin@bright-smile-dental.dev / LocalDev123!) → HTTP 200
`{"status":"mfa_setup_required", ...}`. **This is correct behavior** and validates the live stack:
app boots, DB connected, seeded user resolves, auth works, MFA is enforced. Did NOT script through
the TOTP-setup dance to a token — the authenticated enroll→advance path is already proven by the
real-Postgres integration tests, so the marginal value is low. **API container left running for the
deferred Layer 3 manual FE pass.**

### Remaining layers (increasingly user/ops-dependent)
- **Layer 3 — Frontend manual click-through**: needs app up + browser. FE now builds (140 vitest green);
  the manual pass validates DNC UI / Campaign UI / Builder against a live backend.
- **Layer 4 — Real vendors**: needs full sandbox creds (Twilio auth token, `RESEND_WEBHOOK_SECRET`) —
  answers the open "does Resend echo tags?" question + deliverability + NexHealth staging (Plan 09 runbook).
- **Layer 5 — Prod canary**: feature-flagged, one clinic, watch observability.

### Follow-up commit needed
- `alembic/versions/20260712_sms_wf_attribution.py` (renamed + revision id shortened) — fixes the
  33-char revision id that breaks `alembic upgrade head`.

## 2026-07-15 — Layer 4/5: Plan 09 live sandbox verification (see plan-09-staging-results.md)
Ran Plan 09 flows against the real NexHealth sandbox (`silora-demo-practice`, loc 348511). Discovered the
value the CTO gave was the **API key** (not a webhook secret); `.env` corrected. All 4 flows verified live;
**found + fixed 3 real bugs the mock tests missed:**
- Flow 2 backfill: `GET /appointments` `start_date/end_date` → **`start/end`** (400 otherwise). Fixed.
- Flow 3 subscription: reworked from the dead `/webhooks` (404) to the real **`/webhook_endpoints`** 2-step
  form flow; valid events `appointment_insertion`/`appointment_updated`; endpoint-create returns the signing `secret_key`.
- Flow 4 inbound: reworked parser to `event_name` (+ `.complete` normalize) and the
  `HMAC(secret_key, "{timestamp}.{base64(body)}")` scheme via `signature`/`timestamp` headers — **verified live**
  with a real endpoint secret (valid accepted, tampered/missing-ts → 403).
All v2.0-era-code vs current-v2 (v2.2.2) drift, not v3. **180 NexHealth/Plan-09 tests pass.** Plan 09 ~80% → **~95%**
(full real-appointment round-trip pending — empty sandbox tenant).
> **SUPERSEDED (2026-07-15):** the round-trip is now DONE — **Plan 09 = 100%.** See the next entry below.

## 2026-07-15 — Layer 4/5: Plan 09 REAL webhook round-trip ✅ DONE → Plan 09 = 100% (see plan-09-staging-results.md, Flow 5)
Closed the last ~5%. Over a **cloudflared** quick tunnel (ngrok is unusable in this env — CRL fetch fails),
drove a genuine end-to-end round-trip against the sandbox:
- Registered a real endpoint (`POST /webhook_endpoints` → 201, id 16271, real `secret_key`) + subscriptions
  `appointment_insertion`/`appointment_updated`. Set `NEXHEALTH_WEBHOOK_SECRET` and **recreated** the api
  container (`compose up --force-recreate`; `docker restart` does NOT reload `env_file`) so signature verify
  actually runs. Temporarily wired local "Downtown Clinic" → loc 348511.
- **Booked** appt 1599668687 → real `appointment_insertion` webhook → 200 OK: sig verified, event parsed,
  location resolved, ledger `COMPLETED`, `appointment_working_set` upserted (`scheduled`), workflow enqueued.
- **Cancelled** it → real `appointment_updated` webhook → 200 OK: projection flipped to `cancelled`.
- Both insert + update/cancel paths proven against genuinely NexHealth-signed deliveries.
- Cleanup: appointment cancelled, endpoint deleted (204), secret cleared + container recreated, location
  reverted to NULL, local test rows purged, tunnel stopped. `git status` clean (docs only).
**The "empty tenant" blocker was resolved by booking a bookable provider/type/operatory. Plan 09 = 100%.**
