# QA Plan — Outbound Engagement Engine (Phase 2)

**Owner:** Dev + CTO (frontend) + Ops (infra/staging)
**Goal:** validate all 12 plans (backend + frontend) end-to-end locally, then against real
vendors/staging, then a controlled prod pilot.
**Definition of QA (CTO):** end-to-end local testing + validating production behavior.

QA runs in **layers**, cheapest → most production-like. Each layer gates the next: don't
advance while a lower layer is red.

Legend: ☐ not started · ▶ in progress · ✅ pass · ✗ fail (log it)

---

## Layer 0 — Environment & prerequisites (BLOCKER — do first)

| # | Step | Command / action | Expected |
|---|------|------------------|----------|
| 0.1 | Bring up deps | `make up-deps` (Postgres :5433, Redis :6379) | both healthy; `make health` OK |
| 0.2 | Fresh-DB migration (proves the whole chain, incl. new `20260712_sms_workflow_attribution`) | `make migrate` — or `DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/nexhealth APP_ENV=local uv run alembic upgrade head` | single head applied, 0 errors on a fresh DB |
| 0.3 | Confirm single head | `APP_ENV=local uv run alembic heads` | one head, no divergence |
| 0.4 | **Fix FE node_modules** (blocks ALL frontend QA) | `sudo chown -R $USER nexus-dashboard-web/node_modules` then `cd nexus-dashboard-web && npm install` | `npm run build` / `tsc` runnable |
| 0.5 | Vendor sandbox creds in `.env` | Twilio test SID/token, Resend test key + `RESEND_WEBHOOK_SECRET`, `RETELL_API_SECRET`; (deferred) NexHealth staging | app boots, `make health` OK |
| 0.6 | Seed a test tenant | `python -m src.app.scripts.seed_demo_data` (or existing seed) | 1 InstitutionGroup → Institution → Location, with `twilio_from_number` + `retell_from_number` set, ≥1 Contact w/ phone+email |

**Gate:** app boots, fresh migration clean, FE builds, one seeded tenant. Do not proceed otherwise.

---

## Layer 1 — Automated regression (baseline gate)

| # | Suite | Command | Expected |
|---|-------|---------|----------|
| 1.1 | Backend unit | `APP_ENV=test .venv/bin/pytest tests/unit -q` (or `APP_ENV=local WEBAUTHN_RP_ID=localhost uv run pytest tests/unit`) | green except known env-gated (respx missing, Redis/DB down if not up) |
| 1.2 | Backend integration (real Postgres, testcontainers) | `APP_ENV=test .venv/bin/pytest tests/integration/test_automation_engine_integration.py -q` | 12/12 pass — migration chain + engine + voice on a fresh DB |
| 1.3 | RLS / tenant-scope invariant | `APP_ENV=test .venv/bin/pytest tests/unit/test_tenant_scope_invariant.py tests/unit/test_rbac_route_matrix.py -q` | green — no cross-tenant leak, every route RBAC-classified |
| 1.4 | Frontend typecheck + unit (after 0.4) | `cd nexus-dashboard-web && npm run build && npx vitest run` | tsc clean; builder ~137 tests + Campaign + **DNC UI (first run)** green |

**Note:** the DNC UI and the Plan 08 stat-fidelity fix were written but **never build-verified** (node_modules blocked). 1.4 is their first real check — expect to iterate.

**Gate:** 1.1–1.3 green; 1.4 green after node_modules fix.

---

## Layer 2 — Local end-to-end, backend (real DB + Celery worker, vendors mocked/sandbox)

Run the API + a Celery worker locally (`make up-app` runs both in compose). These exercise the
real durable path. Auth: obtain an institution-admin token via the auth flow, then `Authorization: Bearer <token>`.

### 2.1 SMS reminder campaign (Plan 04 + 01 + 12 + 11)
1. Build/activate a reminder workflow (via API or Builder UI): trigger `appointment_offset`, a `WaitNode`, a `SendSmsNode`.
2. Enroll a contact: `POST /api/automation/workflows/{id}/enroll` `{ "contact_id": "...", "idempotency_key": "qa-1" }`.
3. Advance the timer (or set a short delay).
- **Expect:** run goes ACTIVE → WAITING → resumes → SMS attempted through `SmsService`; `sms_history_logs` row PENDING→SENT; compliance gate passed; body rendered in location TZ.
4. Re-enroll with the **same** `idempotency_key` → **Expect:** no duplicate run (409/skip).
5. Fire the Twilio status callback: `POST /api/v1/twilio/webhooks/sms-status` form `MessageSid=<sid>&MessageStatus=delivered&NumSegments=1&Price=-0.0075&PriceUnit=USD` (valid signature).
- **Expect:** `sms_history_logs` → delivered; **one** `usage_events` row (channel=sms) with segments+cost, tagged `workflow_run_id`+`workflow_id` (the fix). Re-post the callback → still one row (idempotent), cost backfilled not doubled.

### 2.2 SMS opt-out / inbound (Plan 04 + 12)
- Inbound STOP: `POST /api/v1/twilio/webhooks/... From=<patient>&Body=STOP` → **Expect:** suppression written + audit; next send to that number is BLOCKED at the gate (SUPPRESSED row).
- Inbound FR STOP (`ARRÊT`) → same. Inbound free text → persisted `inbound_sms_messages` + staff notification, control flow unchanged.

### 2.3 Email transactional + suppression (Plan 05 + 12 + 11)
1. Workflow with `SendEmailNode`, contact with email on file (no consent record → implied transactional).
- **Expect:** email sent via Resend (sandbox); `usage_events` channel=email emails=1; body carries the signed unsubscribe link.
2. Unsubscribe: `GET /api/email/unsubscribe?token=<signed>` → **Expect:** 200 confirmation; a REVOKED EMAIL consent written; next email BLOCKED at the gate.
3. **Bounce/complaint (the fix):** `POST /api/v1/... /api/email/webhooks/resend` body `{"type":"email.bounced","data":{"to":["<addr>"]}}` (valid signature, **no tags** — the real shape).
- **Expect:** webhook 200 (no 500); resolves institution(s) from the recipient's email_hash; REVOKED EMAIL consent written; subsequent email BLOCKED. Also test the list-shaped-tag variant → scoped directly.
- Recipient with **no** consent record → resolves to 0 (nothing suppressed) — documented limitation; unsubscribe link still covers them.

### 2.4 Voice outbound + outcome loop (Plan 03 + 07 + 11)
1. Workflow with `SendVoiceNode` (`wait_for_outcome=true`), location has `retell_from_number` + agent.
2. Enroll → **Expect:** an `INITIATING` `workflow_voice_attempts` claim committed BEFORE the Retell POST; call placed; run parks WAITING with a safety-timeout timer; metadata carries `workflow_run_id` **and `workflow_id`** (the fix).
3. Fire Retell post-call webhook `POST /api/v1/retell/webhook` (call_analyzed, matching `retell_call_id`, a `disconnection_reason`, `duration_ms>0`).
- **Expect:** attempt row stamped with outcome; run resumes; ConditionNode branches on `call_outcome`; a `usage_events` channel=voice row (minutes+dials) tagged `workflow_run_id`+**`workflow_id`**. Re-post webhook → idempotent (no re-dial, no double meter).
4. Simulate crash-between-POST-and-commit / timeout → **Expect:** claim blocks a re-dial (at-most-once).

### 2.5 Compliance gate — adversarial (Plan 12 — HIGHEST-RISK, spend the most effort)
- **Quiet hours:** enroll so a send falls in the location's quiet window → **Expect:** HOLD (timer scheduled), then resumes + re-checks the gate in-window — never dropped.
- **DNC:** add a do-not-contact for the contact → **Expect:** all channels BLOCKED.
- **Consent basis:** a `marketing` content-class send without express-written consent → **Expect:** BLOCK `*_consent_basis_insufficient`. A `transactional_care` send with identifier on file → allowed (implied).
- **Emergency halt:** `POST /api/automation/workflows/{id}/emergency-halt` and `POST /api/automation/workflows/outbound-halt` → **Expect:** in-flight ACTIVE/WAITING runs terminated + their timers cancelled; new sends blocked.
- **DST:** run near a DST boundary in a non-UTC location → sends land in the correct local window.

### 2.6 Campaigns reach their exits (Plan 06)
- Reminder + Recall enroll live; **Confirmation:** inbound `YES` resumes the WAITING run → `exit-confirmed` + NexHealth `confirm_appointment` write-back (capability-gated, audited). **Reactivation:** a NexHealth appointment.created event resumes → `appointment_booked=true` → `exit-booked`.

### 2.7 Usage reporting (Plan 11)
- After 2.1/2.3/2.4: `GET /api/institution/usage/summary` and `/by-campaign` → **Expect:** per-channel usage/cost; **SMS + voice + email all appear in `/by-campaign`** (the fix — previously only email). `GET /api/group/usage-summary` (GROUP_ADMIN) aggregates the group. Run `python -m src.app.scripts.recompute_usage_rollup` → rollups populate.

### 2.8 Provisioning (Plan 10)
- Set per-institution Twilio creds via the super-admin provisioning API → **Expect:** SMS routes via the tenant sub-account; token never returned (masked SID only); PATCH/DELETE write audit rows. Twilio webhook signature validates with the resolved sub-account token.

**Gate:** all 2.x flows behave as expected against a local DB + worker.

---

## Layer 3 — Frontend QA (after 0.4 unblocks builds)

Run each surface against a real local backend (Layer 2 running).

| # | Surface | Manual click-path | Expect |
|---|---------|-------------------|--------|
| 3.1 | **Builder UI** (Plan 02) | new workflow → add trigger/wait/send nodes → set condition rules → insert merge-fields → `/validate` → publish | canvas works; server validation blocks on errors; **compliance guardrail panel surfaces gate codes**; version history |
| 3.2 | **Campaign UI** (Plan 08) | list → detail → pause/resume/archive → per-campaign halt + run cancel → institution outbound-halt activate/release → **manual enroll** an existing patient | all actions hit real routes; usage/cost cards show **SMS+voice+email per-campaign** (fix); secondary stat cards show **neutral 0 when absent, NOT institution-wide** (fix) |
| 3.3 | **DNC UI** (Plan 08 U-2b — NEW, never build-verified) | add a DNC entry (phone + scope) → list → release (re-enter full phone) | create/list/release hit `/api/institution/do-not-contact`; INSTITUTION_ADMIN-gated; release re-prompts for full phone (masked can't hash-match) |
| 3.4 | Voice profiles/attempts UI (Plan 03 V-8) | **Backend API only; React UI is a known FE gap** — verify via API, flag UI as follow-up | — |

**Cross-check:** FE↔BE contract for each call (payload fields match Pydantic models). Verification found the FE mostly correct; DNC UI + the Plan 08 stat fix are the unproven pieces.

**Gate:** builds clean, all wired actions work in a browser against the real backend.

---

## Layer 4 — Integration / real vendors (sandbox)

| # | Vendor | Test | Expect |
|---|--------|------|--------|
| 4.1 | Twilio | real send + real status callback + real inbound STOP | delivery status + suppression land; usage metered |
| 4.2 | Resend | real send + **real bounce/complaint webhook** to a seeded bad address | **answers the open question: does Resend echo tags?** If not, the email_hash resolver path must fire and suppress. Confirm signature verification. |
| 4.3 | Retell | real outbound call (sandbox number) + real call_analyzed webhook | park→resume→branch on a real call; voice usage metered; disclosure spoken |
| 4.4 | Email deliverability (Plan 05 deferred) | per-tenant domain SPF/DKIM/DMARC + warm-up | **ops/DNS ticket** — the deferred Plan 05 remainder |
| 4.5 | **NexHealth staging (Plan 09 deferred)** | the 4 flows — see `plan-09-staging-runbook.md` | subscription/backfill/reconciliation/reschedule against a live tenant |

**Gate:** real webhooks drive the flows; idempotency holds on real replays.

---

## Layer 5 — Production behavior validation (controlled pilot)

1. Deploy behind the scheduler feature-flag; keep it off until 5.2.
2. **Canary one clinic**, low volume, one campaign (reminder).
3. Watch observability: CloudWatch workflow/usage metrics (backlog, stale timers, failed runs), dead-letter queue, usage rollups auto-refreshing (15-min recompute).
4. Verify the safety rails in prod: emergency halt stops sends; quiet hours hold; an opt-out suppresses within one message.
5. Ramp volume only after a clean canary window.

**Gate:** clean canary (no cross-tenant leak, no double-send/dial, opt-outs honored, halt works) before ramp.

---

## Per-plan QA focus (coverage matrix — nothing missed)

| Plan | Must prove | Primary layer |
|------|-----------|---------------|
| 01 Engine | enroll→wait→resume→exit; stale-claim recovery; halt cascade; version pinning; RLS | 1.2, 2.1, 2.5 |
| 02 Builder UI | build→validate→publish; compliance panel; merge-fields | 1.4, 3.1 |
| 03 Voice | park→resume→branch; crash-safe claim (no double-dial); disclosure | 2.4, 4.3 |
| 04 SMS | gated send; STOP/START; delivery webhook; inbound routing; idempotency | 2.1, 2.2, 4.1 |
| 05 Email | send; unsubscribe; **bounce/complaint suppression (fixed)**; deliverability (deferred) | 2.3, 4.2, 4.4 |
| 06 Campaigns | 4 templates enroll + reach exits (confirm/booked reachable) | 2.6 |
| 07 Callback | inbound needs_callback → gated voice; loop-guard | 2.4 |
| 08 Campaign UI | manage/halt/enroll/cancel; usage cards (SMS+voice fixed); DNC UI | 3.2, 3.3 |
| 09 Data Layer | **staging: subscription/backfill/reconciliation/reschedule** | 4.5 (runbook) |
| 10 Provisioning | tenant creds route; token never leaked; audit | 2.8 |
| 11 Usage/Cost | all-channel metering exact; rollup; /by-campaign (fixed); group | 2.7 |
| 12 Compliance | gate on every send; basis hard-block; implied; DNC; DST | 2.5 |

---

## Cross-cutting (test ACROSS plans, not per-plan)

- **Multi-tenant isolation (RLS):** every read/write scoped; no cross-tenant leak. (1.3 + spot-checks in Layer 2.)
- **Idempotency:** replay EVERY webhook (Twilio/Resend/Retell/NexHealth) → no double send/dial/count. Highest-value cross-cutting test.
- **Timezone/DST:** sends land in clinic-local windows across a DST boundary.
- **Compliance safety:** the gate is the patient-safety/legal rail — test adversarially (2.5); it's the single highest-risk surface.

---

## Known-open going into QA (from verification + register)

- **Deferred (pending CTO Monday sign-off):** Plan 05 sending domain (DNS/vendor, 4.4), Plan 09 staging validation (4.5).
- **P2 flagged (decisions, not blockers):** Plan 12 bilingual outbound footer + group-scope DNC creation; Plan 02 ETag on live-campaign edit; Plan 07 resolve-during-ETA guard. (See `../qa-prep-p2-tail/task_plan.md`.)
- **FE unverified until 0.4:** DNC UI, Plan 08 stat-fidelity fix.

## Sign-off checklist (pilot gate)
- ☐ Layer 0 green (fresh migration, FE builds, seeded tenant)
- ☐ Layer 1 green (backend + FE suites)
- ☐ Layer 2 all flows behave (esp. 2.5 compliance)
- ☐ Layer 3 all three UIs work in a browser
- ☐ Layer 4 real webhooks drive flows + idempotent replays; **Resend tag question answered**
- ☐ Plan 09 staging runbook passed
- ☐ Layer 5 clean canary before ramp
- ☐ CTO sign-off on the two defers + any P2 decisions
