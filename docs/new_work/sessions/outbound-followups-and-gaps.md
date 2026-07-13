# Outbound Engagement Engine — Follow-ups & Gaps Register

**Last updated:** 2026-07-14
**Purpose:** the **single source of remaining work** for Phase 2 — every open gap, deferral, bug, and
follow-up across all 12 plans, de-duplicated and prioritized. This is the "what's left / why" companion to
`verification-phase2-v2/report.md` (which is the "where we are / status" document). To avoid duplication:
**status + percentages live in the report; actionable remaining work lives here.**

---

## Decision Log

### 2026-07-12 — Plan 05 & Plan 09 remainders proposed for QA-deferral (PENDING CTO SIGN-OFF)

Both remaining plans are at ~80% with **no code work left** — their remainders are **external / staging**,
not implementation. Proposed as deferrals so the team can move into the verification + QA phase this week
per the CTO's directive. **Not final — awaiting CTO sign-off (back Monday 2026-07-13).**

- **Plan 05 — Outbound Email → DEFER remainder to QA/ops.** Remaining = per-clinic sending domains,
  SPF/DKIM/DMARC DNS, domain warm-up, high-volume deliverability verification, and optional HTML/branded
  templates (items **E-3, E-4**). **Rationale:** all external DNS/vendor + scale-deliverability + optional
  polish — **not a functional gap**. The channel is launch-compliant today (transactional sends, one-click
  unsubscribe, bounce/complaint suppression, usage metering all shipped). Shared platform Resend domain works
  for a pilot. → belongs to QA/ops, not a dev sprint.
- **Plan 09 — Integration & Data Layer → DEFER remainder to QA.** Remaining = validate the four flows against a
  real/staging NexHealth tenant (items **D-5, D-6**). **Rationale:** code is complete and unit-tested (mocked
  client); the last 20% is *staging validation*, which is blocked on prerequisites the dev cannot self-provision:
  staging `NEXHEALTH_API_KEY`, staging `NEXHEALTH_WEBHOOK_SECRET`, a real `InstitutionLocation`
  (`nexhealth_subdomain` + `nexhealth_location_id`), a public HTTPS callback
  (`https://<staging-api>/api/v1/nexhealth/webhooks/appointments`), and a running staging worker/beat. → this
  **is** the QA/staging phase; it cannot be "implemented," only validated.

**Consequence:** with both remainders deferred, no feature-implementation work remains for Phase 2. Next step is
the CTO's requested implementation-verification pass (4-bucket classification across all sessions), then QA.
A Plan 09 staging runbook will be prepared so the moment credentials exist, validation is a ~1-hour exercise.

### 2026-07-14 — Verification (v3) + fixes + QA executed

- **Verification pass done** → `verification-phase2-v3/report.md` (4-bucket classification, all 12 plans).
  Headline: Bucket-3 (unnecessary/remove) nearly empty; the codebase is clean.
- **3 blocking fixes shipped** (`qa-prep-3-fixes/`): **Plan 05** Resend bounce/complaint webhook now
  resolves institution(s) from the recipient `email_hash` and suppresses (was: suppressed nothing in
  prod — tag mismatch + Resend omits tags); **Plan 11** voice + SMS now stamped with `workflow_id` so
  they appear in `/by-campaign` (was: only email); **Plan 08 U-2b** staff DNC admin UI built.
- **P2 tail cleared** (`qa-prep-p2-tail/`): removed the dead Plan-01 registry seam; fixed stale
  Plan-10/11 doc comments; Plan-08 stat cards no longer fall back to institution-wide totals. 4 P2
  items intentionally left as decisions (bilingual footer, group-DNC creation, Plan-02 ETag, Plan-07 ETA guard).
- **QA Layers 0–2 executed green** (`qa-plan/`): fresh migration (fixed a 33-char revision id → `20260712_sms_wf_attribution`),
  backend 1494 tests + FE 140 vitest + tsc clean, and NEW real-Postgres channel/gate integration tests
  (`tests/integration/test_outbound_channels_integration.py`) proving all 3 fixes E2E. Live API smoke: stack
  boots + auth/MFA enforced. Layer 3 (manual FE) deferred; Layers 4–5 (vendors, prod) ops-gated.

**Legend:** ✅ done · ❌ dropped (product-owner decision) · ⬜ open.
**Priority:** **P0** = fix before any real/at-scale send (correctness/security/patient-safety/legal) ·
**P1** = needed for a complete, trustworthy launch · **P2** = hardening / scale / polish.

**Product-owner decision (2026-07-04): NO caps or limits on clinics/locations, and no tenant-based caps.**
Frequency caps, spend/budget caps, blast-radius/step-up gates, and per-location outbound concurrency caps
are **dropped, not deferred**. Non-cap vendor-throughput *smoothing* and per-clinic *isolation* remain valid.

---

## ✅ Done (this + prior sessions — for the record, not action)

- **P0-1** NexHealth webhook fails closed in prod (startup guard in `config.py` + 403 in `_verify_signature`).
- **P0-2** Email consent keyed on an **email identity** (`ConsentRecord.email_hash` + `hash_email`; gate split
  email vs phone; migration `20260705_consent_email_identity`) — email-only contacts no longer blocked `no_phone`.
  *(Fixed the consent key/identity; the separate email/voice consent-**capture** gap is XC-6.)*
- **P0-3** Voice **idempotency guard** — `VoiceNodeExecutor` skips re-dial if a completed `call_placed` step exists.
- **XC-1** Send-time idempotency for **all three channels** (`runtime.already_sent` checked first in the SMS/
  email/voice executors → skip + advance if already sent), **plus** a latent quiet-hours hold→resume
  unique-index collision fixed (`begin_step` now allocates the next `attempt_number`). Verified 1329 unit +
  7 integration. Residual crash-window tracked as **XC-1b** below. Session: `outbound-xc1-send-idempotency/`.
- **Plan 12 semantic layer:** content-class/PHI validator wired into publish + `/validate`; AI-voice disclosure
  injection; **bilingual FR STOP**; **do-not-contact scope tiers** + DNC now enforced on **all** channels (voice/email were a hole).
- **Plan 05 email compliance:** one-click unsubscribe + Resend bounce/complaint suppression shipped; revoked EMAIL
  consent is written by email hash, and campaign email sends with a Resend idempotency header.
- **Plan 12 implied transactional consent:** transactional/care email + voice can send on implied consent when the
  channel identifier is on file; commercial/recall still require express consent.
- **Plan 10 provisioning closeout:** secure tenant credential storage/routing + admin status/config is enough for
  current scope. CTO confirmed automated vendor setup/onboarding and a new persisted onboarding/readiness lifecycle
  are not required now.
- **Plan 02:** builder wires `/validate` `/versions` `/merge-fields`; merge-field drift fixed; the validation panel
  surfaces the new compliance codes automatically (no FE change needed).
- **Engine hardening (finalize):** inline path unified onto gated `build_dispatcher` (kills Finding A); quiet-hours
  **hold-and-resume** (Finding B); real emergency-halt (terminates in-flight runs + cancels timers); stale-claim
  recovery wired; enrollment idempotency race fixed; template instantiate fixed; two fresh-deploy migration bugs +
  two Alembic heads fixed.
- **Plan 09 (finalize):** cancellation handling + `PmsLiveRevalidationService` at dispatch (Finding E, minus reschedule re-enroll); real recall pull.
- **Plan 07 (merged 2026-07-04, `97fe227`):** AI-callback core — `callback_requested` trigger, `CallbackTriggerService`,
  `trigger_callback_workflows` task, Retell webhook hook. Merged into `ali/phase-2` with zero conflicts; 1340 unit green. Residual: CB-2..CB-4.
- **Test-suite green (2026-07-04):** 1325 unit + 6 integration, 0 failures. Cleared: a **real auth bug**
  (`RefreshTokenService._encode_session` stacked decorator broke login on Python 3.13+; fixed — *auth subsystem,
  flag to auth owner*); re-enabled the PHI tenant-scope invariant (was silently disabled by a cp1252 read) + fixed a
  stale allowlist line; RBAC route-matrix drift (6 automation routes added); stale engine/event tests; `respx` dev dep added.

## ❌ Dropped (product-owner: no caps) — do not build
- Frequency caps (≤1/day, ≤3/week) — was Plan 12 / TCPA-exemption condition; risk accepted.
- Spend / budget caps; blast-radius / projected-spend step-up gate (Plan 12).
- Per-location outbound **concurrency cap** (Plan 03). *(Per-clinic Retell workspace isolation / BYO-SIP is NOT a cap — see Plan 03 P2 below.)*

---

## ⬜ Open work — by plan

### Cross-cutting (highest-leverage)
- **XC-1b (P1/P2) Crash-window idempotency — EMAIL ✅, VOICE ✅, SMS open.** Email: Resend
  `Idempotency-Key: email:{run}:{node}` header (vendor-deduped). Voice (2026-07-04/05): P9 committed
  `INITIATING` claim before the Retell POST + skip-if-claimed, plus XC-1b option A (timeout terminal, no
  re-dial) — closes both the crash tail and the lost-response-timeout double-dial. **Still open:** SMS (Twilio)
  crash-window — apply the same committed-claim (a `workflow_sms_attempts` table) and/or terminal-timeout policy.
- **XC-2 (P2) Channel integration tests.** SMS/email/voice are unit-tested with mocked vendors only; extend the
  real-Postgres engine integration pattern to a sandboxed Twilio/Resend/Retell path.
- **XC-3 (P2) Migration convention.** Every post-baseline migration must be idempotent (`IF NOT EXISTS`) — the
  baseline builds schema from live metadata. Broke fresh deploys twice (both fixed). Document it + audit remaining migrations.
- **XC-4 (P2) Ops rollout.** CloudWatch alarms for workflow/usage metrics (backlog, stale timers, failed runs);
  operator runbooks (pause/halt/dead-letter replay); feature-flag the scheduler for staged go-live.
- **XC-5 (P2) Non-cap paced dispatch.** Global smoothing against the shared NexHealth ~1000/min key + Twilio/Retell
  limits (not a per-clinic cap). Only jitter ships today. Coordinate with Plan 09 backfill.
- **XC-6 (P0/P1) Consent-CAPTURE path — VOICE ✅ done, EMAIL commercial capture still open.** The gate enforces per-channel
  consent but the writers only wrote SMS. **Closeout 2026-07-04:** made `record_consent`/`record_consent_identity`
  channel-generic + added `has_consent_record`; the AI-callback path now records an express **VOICE** consent on
  the inbound callback request (if none exists), so **Plan 07 voice callbacks are functional end-to-end** (real-DB
  test). **Closeout 2026-07-07/08:** transactional/care EMAIL and VOICE now send on implied consent when the
  identifier is on file, while revoked consent/DNC still block. **Still open:** express-consent capture for
  commercial/recall email/voice is deferred with the client-deferred lead-intake pipeline.

### Plan 03 — Outbound Voice (~70–75% — outcome loop + consent basis built 2026-07-04)
Implemented 2026-07-04 (`outbound-03-voice-implementation/`) — Plan 03 now ≈70–75%.
- **V-1 ✅ Outcome feedback loop.** Wait-for-outcome park (`SendVoiceNode.wait_for_outcome`) → dispatcher parks
  WAITING with a safety timer → the post-call webhook correlates by `retell_call_id` → `voice_outcome` maps the
  Retell `disconnection_reason` → `resume_voice_outcome` writes `call_outcome` and resumes → a ConditionNode
  branches (no-answer→retry, voicemail→SMS, answered→done). Real-DB integration test.
- **V-2 ⬜ Disclosure ENFORCEMENT (text done).** The `compliance_disclosure` dynamic variable is injected; the
  prompt-speaks-it verification (via get-retell-llm) is brittle → follow-up; spoken-opt-out→suppression is
  **blocked (A-8, ambiguity-review)** — the Retell post-call DNC-intent field shape is unconfirmed.
- **V-3 ✅ Consent basis.** `ConsentRecord.basis` + gate content-class matrix (marketing→express_written,
  recall→express, care→any); migration `20260707`.
- **V-4 ✅ Dedicated data model.** `outbound_voice_profiles` (per-location agent/number/config) +
  `workflow_voice_attempts` (run/step/attempt link, `retell_call_id`, masked endpoints, lifecycle status,
  `dial_outcome`) landed as models + idempotent RLS migration `20260708_voice_data_model` +
  `voice_attempt_recorder` seam. Executor resolves the profile (override-with-fallback) and records an
  attempt row per placed call; `resume_voice_outcome` stamps the outcome (incl. raw `disconnection_reason`,
  threaded webhook → task → `stamp_attempt_outcome` on 2026-07-05). *Deferred sub-item:* optional
  `calls`→run linkage columns (not required by P9/V-8; no consumer — skipped).
- **V-5 ✅ Voice usage metering** — shipped via Plan 11 **M-1** (Retell post-call webhook emits voice minutes/dials,
  attributed to the run via `metadata.workflow_run_id`). Voice **cost** intentionally reports $0 under Plan 11's
  product Option B until an approved rate card exists.
- **V-6 ✅ Transient retry** (refined by XC-1b option A, 2026-07-05). Executor classifies **5xx → transient**
  (re-raise for Celery retry until `max_attempts`, then fail), **4xx → permanent** (fail), **timeout/network →
  ambiguous** (`RetellAmbiguousError`: fail, NO retry, keep P9 claim blocking). Wires `SendVoiceNode.max_attempts`.
- **V-7 ✅ Client extraction.** `RetellOutboundClient` (mockable, error-classifying) extracted; dead voice
  dispatch fallback removed (N-1).
- **P9 ✅ Crash-safe committed claim.** Executor commits an `INITIATING` `workflow_voice_attempts` row
  before the Retell POST; `voice_send_already_claimed` skips a re-dial when a committed non-FAILED claim
  exists (closes the crash-between-POST-and-commit tail, at-most-once); transient/permanent errors mark the
  claim FAILED + commit so a V-6 retry re-dials. **Timeout residual RESOLVED (XC-1b option A, 2026-07-05):** a
  timeout/network error is now terminal (no retry) and leaves the claim INITIATING/blocking, so a lost-response
  timeout can neither retry nor be redelivered into a second dial (at-most-once). 5xx (call definitely not placed)
  still retries.
- **V-8 API ✅ / FE ⬜.** Backend done (API-first): `src/app/api/routes/outbound_voice.py` — `/api/outbound-voice`
  profiles CRUD (gate institution/location-admin; 409 on one-active-per-location) + attempts drill-down GET
  (gate institution/location-user; masked numbers) + `list_voice_attempts` helper; registered + RBAC-matrix
  classified. **Remaining = React UI** (fast follow): mirrors `LocationAdminPanel` + `CampaignDetail` +
  `RevealablePhone` (see `outbound-03-voice-ui-and-closeout/findings.md` F-2).
- **V-9 (P2, non-cap) Per-clinic Retell workspace isolation (BYO-SIP)** — single platform `retell_api_secret` today
  (scope §3.5/§7.2). Isolation, not a numeric cap.

### Plan 04 — Outbound SMS (~70%)
- **S-1 ✅ (via XC-1)** SMS send-time idempotency — the shared `already_sent` guard now covers SMS (skip + advance
  if already sent). Residual crash-window = **XC-1b**. A dedicated `workflow_sms_attempts` table is optional polish.
- **S-2 (P1) Free-text inbound routing** — replies are ignored (empty TwiML, no persistence/notification). Build
  `inbound_sms_messages` + `InboundSmsRoutingService` (staff notification at minimum).
- **S-3 ✅ `sms_history_logs` workflow linkage (2026-07-14)** — added nullable `workflow_run_id`/`workflow_id`
  (migration `20260712_sms_wf_attribution`), stamped at send time and carried to the delivery-status usage
  event so SMS now appears in `/by-campaign`. (`step_id`/`attempt_number`/`price_*` still optional, not needed.)

### Plan 05 — Outbound Email (~70%; launch-compliant v1)
- **E-1 ✅ Unsubscribe.** Signed one-click unsubscribe link + email suppression shipped.
- **E-2 ✅ Bounce/complaint webhook.** Resend webhook signature verification + suppression from hard bounce/complaint shipped.
- **E-3 (P2) HTML/branded body** — optional polish; plain text v1 is the launch-compliant slice.
- **E-4 (P2/external) Per-tenant sending domain** — SPF/DKIM/DMARC + warm-up + encrypted per-tenant Resend key /
  `EmailSendingProfileService` if scale requires it (see Plan 10).
- **E-5 ✅ Attempt/audit log not required.** Current usage/campaign attribution lives on `usage_events` and workflow
  steps; unsubscribe/bounce suppression does not need a `workflow_email_attempts` table.
- **E-6 ✅ Email usage metered.** Sends record `emails=1`; cost remains $0 by Plan 11 product Option B.

### Plan 06 — Four Live Campaigns (100% for agreed scope; updated 2026-07-07)
- **C-1 ✅ Confirmation branch capture.** Inbound SMS confirmation replies (`YES`, `Y`, `CONFIRM`, `C`, `1` as
  bare tokens) now write `appointment_status="confirmed"`, cancel the wait timer, and resume the matching WAITING
  confirmation run to `exit-confirmed`.
- **C-2 ✅ PMS confirmation write-back.** `NexHealthAdapter.confirm_appointment` capability-gates
  `PATCH /appointments/{id}` with `{"appt":{"confirmed":true}}`; write-back is fail-open and audited.
- **Reactivation booked branch ✅.** NexHealth appointment created/updated events now resume matching WAITING
  reactivation runs with `appointment_booked=true`, so `exit-booked` is reachable.
- **C-3 ✅ Sales Qualification campaign — DROPPED (product decision).** Absent by design because there is no
  lead-intake pipeline to enroll it meaningfully. Revisit only once lead intake exists.
- **C-4 / C-5 not required for current feature completion.** DB-backed versioned templates, normalized outcome
  mapping, channel-order/fallback, and attempt-ceiling config are maintainability/configuration work, not blockers
  for the agreed live campaign scope.

### Plan 07 — AI Callback Handling (~60% — core v1 merged 2026-07-04, Hammad `97fe227`)
- **CB-1 ✅ (core merged)** — `callback_requested` trigger + `CallbackTriggerService` + `trigger_callback_workflows`
  task + Retell webhook hook (loop-guarded). Enrolls via `enroll_and_start_workflow_run` → inherits the gate,
  revalidation, and XC-1 idempotency. Opt-in = activating a `callback_requested` workflow.
- **CB-2 ✅ (closeout 2026-07-04).** Quiet-hours defer-and-resume is the intended behavior — documented and the
  dev's `outbound-07-ai-callback/findings.md` D2/D4 notes reconciled. Added a **double-contact guard**:
  `_trigger_callback_async` skips if the source Call is already `callback_resolved` (residual: a resolve during
  the ETA delay isn't caught).
- **CB-3 ✅ (closeout 2026-07-04).** Voice-consent capture landed (see XC-6) — the AI-callback path records an
  express VOICE consent on the inbound request, so callbacks now pass the gate and place calls end-to-end. Verified
  by a real-DB test. Callback workflows can now use Plan 03's outcome loop via `SendVoiceNode.wait_for_outcome`.
- **CB-4 ✅ not required.** Packaged AI-callback template + optional `callback_workflow_links`/settings tables were
  superseded by the leaner opt-in-via-activation design. Callback workflows inherit Plan 03's outcome loop when
  their `SendVoiceNode.wait_for_outcome` is enabled.

### Plan 08 — Campaign Management / Analytics UI (100% essential scope)
- **U-1 ✅ Manual enrollment UI.** Existing-patient manual enrollment is now surfaced on campaign detail using the
  existing single-enroll backend. **CSV import is not required for current scope** and stays deferred because it adds
  PHI/consent/retention decisions.
- **U-2 ✅ Emergency-halt UI.** Institution-wide halt status/activate/release and per-campaign emergency halt are
  surfaced in the campaign UI. Backend `/outbound-halt` literal routes were also moved before `/{workflow_id}`.
- **U-2b ✅ Privileged institution DNC admin endpoint + UI (2026-07-14)** — backend `POST/DELETE/GET
  /api/institution/do-not-contact` (Plan 12) + a React admin page (`DoNotContactAdmin.tsx`, INSTITUTION_ADMIN,
  api-client + test). *Group/DSO-wide DNC creation remains a GROUP_ADMIN follow-up (gate already honors GROUP scope).*
- **U-3 ✅ essential analytics.** Campaign detail consumes Plan 11 `/institution/usage/summary` + `/by-campaign` for
  usage/cost cards. Attributed revenue, outcome-rate definitions, trends, and group-level dashboards are not required
  for current scope.
- **U-4 (P2) Operations page** — dead-letter/replay, stale-timers, run-detail timeline. Run cancel is now exposed
  from the campaign detail table. Full ops/replay and timelines are high-volume support tooling, not current blockers.
- **U-5 (P2) SSE real-time** — pages are manual-refresh; wire `workflow_run_updated`. Native archive `confirm()` is
  fixed; SSE and location scoping are not required for current scope.

### Plan 09 — Integration & Data Layer (~80%; staging verification pending)
- **D-1 ✅ Reschedule re-enroll at the new time.** `appointment_working_set` detects start-time changes and the
  appointment idempotency key now includes the appointment start time, so rescheduled reminders are re-timed instead
  of silently dropped.
- **D-2 ✅ Revalidation freshness window.** `PmsLiveRevalidationService` checks recent projection rows first and
  falls back to live NexHealth only when needed.
- **D-3 ✅ Disposable read-model core built.** `appointment_working_set`, `nexhealth_webhook_events`,
  `nexhealth_webhook_subscriptions`, initial REST backfill, and reconciliation sweep exist and are locally tested.
- **D-4 ✅ Event-level idempotency/perf core built.** Webhook ledger dedupes event handling; run lookup indexes support
  cancel/reschedule repair.
- **D-5 ⬜ NexHealth staging verification.** Subscription create/list/health, real webhook payloads, backfill
  pagination/filtering, and reconciliation behavior still need proof against a live staging tenant.
- **D-6 ⬜ Post-staging recall projection decision.** Decide whether `recall_eligibility_working_set` is needed after
  seeing real NexHealth recall/backfill behavior.

### Plan 10 — Per-Tenant Provisioning (100% agreed scope)
- **PR-1 ✅ (closeout 2026-07-04).** Provisioning credential changes (`admin_institutions` PATCH + DELETE) now
  `log_audit(INSTITUTION_UPDATE)` with the actor + masked metadata (never the token/SID).
- **PR-2 ✅ Persisted onboarding/readiness lifecycle not required.** Existing status/readiness visibility is enough for
  current scope; do not add new workflow state just for plan completion.
- **PR-3 ✅ Vendor setup/onboarding automation not required.** Twilio sub-account creation, A2P/toll-free registration,
  Resend DNS/domain setup, warm-up, and Secrets Manager onboarding automation remain external/manual operational work.

### Plan 11 — Usage & Cost Reporting (100%) — complete for agreed scope (updated 2026-07-07)
- **M-1 ✅ Voice usage metering.** Retell post-call webhook emits `channel=voice` usage (minutes + dials),
  attributed to the workflow run via echoed `metadata.workflow_run_id` (`retell/webhooks.py:416-445`;
  `VoiceNodeExecutor` stamps the metadata). Voice **cost** intentionally remains $0 under product Option B because
  Retell does not provide a per-send price and no approved rate card exists.
- **M-2 ✅ Rollups + reporting API.** `usage_cost_rollups` table + `UsageRollupService`
  (UPSERT-from-SELECT, location + institution daily rollups; `services/usage_rollup.py`, migration
  `20260706_usage_cost_rollups`) + runner `recompute_usage_rollup.py`; reporting API `/institution/usage`
  (`GET /summary`, `GET /by-campaign`, RLS-enforced). **Consumed by Plan 08 campaign usage cards.**
- **M-3 ✅ Cost fidelity for available provider prices.** SMS late-price callbacks now backfill NULL cost/segments
  on the existing idempotent usage event (`UsageMeteringService._backfill_costs`). Email and voice cost remain $0
  by product Option B because providers do not supply per-send prices; this is a reporting-policy decision, not an
  implementation gap.
- **M-4 ✅ Campaign attribution.** `usage_events.workflow_run_id` + `workflow_id` added (migration
  `20260706_usage_event_campaign_tags`); `record()` persists them; per-campaign spend works via `/by-campaign`.
- **M-5 ✅ Budgets dropped; essential dashboards moved to Plan 08 and shipped.** `usage_budgets`/budget caps are
  dropped by the no-caps product decision. Plan 08 consumes the shipped usage APIs for campaign usage/cost cards.
- **M-6 ✅ Cost estimation deliberately not built.** No `estimated` flag/rate-card estimate because no approved
  business rates exist; exact usage counts still report across SMS/email/voice.
- **M-7 ✅ Group endpoint + scheduled recompute shipped; alarms are hardening.** `GET /api/group/usage-summary`
  exposes DSO/group aggregation, and infra schedules `RecomputeUsageRollup` every 15 minutes. Ingestion-lag/
  rollup-failure alarms and deeper RLS/integration tests are operational hardening, not Plan 11 blockers.

### Plan 12 — Compliance & Consent (100% agreed scope)
- **CO-1 ✅ Named `ConsentService`/`SuppressionService` not required.** Logic lives in the gate +
  channel-generic `SmsComplianceService` helpers.
- **CO-2 (P2) US cross-timezone quiet hours** — clinic-TZ only; patient-local quiet-hours are future policy work if required.
- **Commercial express-consent capture** is deferred with client-deferred lead intake; gate correctly blocks
  marketing/recall email/voice meanwhile.

---

## Suggested execution order
1. ✅ **P0 send-safety bundle** + **Plan 12 semantic layer** — DONE.
2. ✅ **XC-1 send-time idempotency (all channels)** — DONE (+ latent hold-resume collision fixed).
   Remaining: **XC-1b** crash-window (committed-before-send claim / provider idempotency key) before high volume.
3. ✅ **Plan 11 usage/cost** — complete for agreed scope: voice/SMS/email usage ingestion, campaign attribution,
   institution reporting, scheduled rollups, SMS late-price backfill, and group usage summary are shipped. Budgets
   are dropped by the no-caps decision; voice/email cost stays $0 by product Option B; essential campaign usage UI is
   shipped in Plan 08.
4. **Plan 09 resilient core** (D-1..D-4) — reschedule re-enroll, freshness window, projections/backfill/reconciliation.
5. ✅ **Plan 05 email compliance** — unsubscribe + bounce/complaint shipped. Remaining scale email is per-tenant
   domain/DNS/warm-up if launch scale requires it.
6. **Plan 06 differentiators** (C-1..C-3) — PMS write-back (fixes dead confirm-branch), Sales Qualification.
7. ✅ **Plan 07 AI callback** — core v1 merged (2026-07-04). Remaining: CB-2/CB-3 confirmations + CB-4 template/tables.
8. **Plan 03 outcome feedback loop** (V-1..V-3). Plan 10 is complete for agreed scope.
9. ✅ **Plan 08 essential operator UI** — complete. CSV/revenue/timeline/ops/SSE remain deferred/not-required unless
   future launch workflows explicitly need them.
