# Outbound Engagement Engine — Follow-ups & Gaps Register

**Last updated:** 2026-07-04
**Purpose:** the **single source of remaining work** for Phase 2 — every open gap, deferral, bug, and
follow-up across all 12 plans, de-duplicated and prioritized. This is the "what's left / why" companion to
`verification-phase2-v2/report.md` (which is the "where we are / status" document). To avoid duplication:
**status + percentages live in the report; actionable remaining work lives here.**

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
- **XC-6 (P0/P1) Consent-CAPTURE path — VOICE ✅ done, EMAIL still open.** The gate enforces per-channel
  consent but the writers only wrote SMS. **Closeout 2026-07-04:** made `record_consent`/`record_consent_identity`
  channel-generic + added `has_consent_record`; the AI-callback path now records an express **VOICE** consent on
  the inbound callback request (if none exists), so **Plan 07 voice callbacks are functional end-to-end** (real-DB
  test). **Still open — EMAIL capture (P1):** the writer is now channel-generic, but nothing captures email
  consent, so **Plan 05 email sends remain blocked-by-default** until an email opt-in/intake flow records it
  (Plan 05 / Plan 12). General voice consent (non-callback triggers, e.g. Recall/Sales) also still needs an
  intake path. *(P0-2 fixed the email consent key, not this capture gap.)*

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
- **V-5 (P1) Voice usage metering** — deferred to **Plan 11** (M-1; voice emits no `UsageEvent`).
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
- **S-3 (P2) `sms_history_logs` workflow linkage** — add `workflow_run_id`/`step_id`/`campaign_id`/`attempt_number`/
  `provider_segments`/`price_*` so delivery joins to a run/attempt (linkage currently only on the separate `usage_events` row).

### Plan 05 — Outbound Email (~30–35%)
- **E-1 (P1) Unsubscribe** — link/token + email suppression. Legal minimum (CASL/CAN-SPAM); do not launch email without it.
- **E-2 (P1) Bounce/complaint/delivered webhook** — `EmailWebhookService` + signature verify + suppression from hard bounce/complaint.
- **E-3 (P2) HTML/branded body** (plain text only today) + email-specific merge-field allowlist.
- **E-4 (P2) Per-tenant sending domain** — SPF/DKIM/DMARC + warm-up + encrypted per-tenant Resend key + `EmailSendingProfileService` (see Plan 10).
- **E-5 (P2) Email attempt/audit log** (`workflow_email_attempts`) — no per-send record today → future bounce reconciliation impossible.
- **E-6 (P1) Email cost not captured** — metered at $0 (see M-3).

### Plan 06 — Four Live Campaigns (~50–55%)
- **C-1 (P1) Confirmation "confirmed"-branch is dead code** — nothing writes `appointment_status` into run state, so
  the confirm branch is unreachable (always exits `no_response`); mirrored in the reactivation `appointment_booked`
  branch. Fix requires the inbound-response→run linkage (ties to S-2 / V-1) and/or PMS write-back (C-2).
- **C-2 (P1) PMS confirmation write-back** — no `update_appointment` capability exists; Confirmation can't write status back to NexHealth.
- **C-3 (P1) Sales Qualification campaign** — absent (the 4th template slot is a non-plan `reactivation` campaign); needs new-contact/CSV enrollment.
- **C-4 (P2) DB-backed versioned templates** — templates are in-code dataclasses, not `workflow_templates`/`_versions`; edits can't be versioned/propagated safely.
- **C-5 (P2) Normalized outcome mapping** + channel-order/fallback/attempt-ceiling config.

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
  by a real-DB test. (Still fire-and-forget until Plan 03's outcome loop, V-1.)
- **CB-4 (P2) Packaged AI-callback template + optional `callback_workflow_links`/settings tables** (deferred by the
  leaner opt-in-via-activation design). Also inherits Plan 03's fire-and-forget voice limits (see V-1).

### Plan 08 — Campaign Management / Analytics UI (~22%)
- **U-1 (P1) Enrollment UI + CSV import** (mapping/validate/preview) + `campaign_enrollment_batches`. Backend
  single/bulk enroll exists but is unconsumed by the UI.
- **U-2 (P1) Emergency-halt UI** — backend routes exist, no frontend surface.
- **U-2b (P1) Privileged institution/DSO-wide DNC admin endpoint + UI** — the `set_do_not_contact` writer exists (Plan 12); expose it.
- **U-3 (P2) Analytics/reporting page + attributed revenue** — blocked on Plan 11 rollups (M-2).
- **U-4 (P2) Operations page** — dead-letter/replay, stale-timers, run-detail timeline.
- **U-5 (P2) SSE real-time** — pages are manual-refresh; wire `workflow_run_updated`. Fix native `confirm()` → app Dialog; add location scoping.

### Plan 09 — Integration & Data Layer (~40%)
- **D-1 (P1) Reschedule re-enroll at the new time** — a rescheduled appointment's reminder is **silently dropped**
  (time-independent idempotency key dedupes the re-enroll; revalidation then skips the stale-time send). Needs the time-aware working-set (D-3).
- **D-2 (P1) Revalidation freshness window** — dispatch revalidates *every* appointment run live → an 800-patient
  9 AM batch = ~800 burst NexHealth calls. Add a recent-webhook freshness window + shared-key budget.
- **D-3 (P2) Disposable read-model core** — `appointment_working_set` / `recall_eligibility_working_set` projections,
  `nexhealth_webhook_subscriptions` + lifecycle, a webhook **event ledger** (edge idempotency + audit), initial REST
  **backfill** (go-forward-only gap today), and a paced **reconciliation sweep**. Enables D-1 + out-of-order/staleness resilience.
- **D-4 (P2) Perf** — whole-table workflow scan per webhook; no event-level idempotency (dup deliveries re-run).

### Plan 10 — Per-Tenant Provisioning (~25%)
- **PR-1 ✅ (closeout 2026-07-04).** Provisioning credential changes (`admin_institutions` PATCH + DELETE) now
  `log_audit(INSTITUTION_UPDATE)` with the actor + masked metadata (never the token/SID).
- **PR-2 (P2) First-class readiness *state* model** — readiness is computed on read; no persisted status/lifecycle.
- **PR-3 (P2) Provisioning automation** — Twilio sub-account creation, A2P 10DLC / toll-free registration, email
  domain SPF/DKIM/DMARC + warm-up (all manual today); Secrets Manager for tenant creds; per-location sub-account scoping.

### Plan 11 — Usage & Cost Reporting (~15%)
- **M-1 (P1) Voice usage metering** — voice emits no `UsageEvent`; wire it in the Retell **post-call webhook** (has duration/minutes).
- **M-2 (P1) `usage_cost_rollups`** (location→institution→DSO) + `UsageRollupService` + reporting API — **blocks Plan 08 analytics (U-3)**.
- **M-3 (P1) Capture email cost** (Resend cost currently unmetered → $0); handle late price-update webhooks (currently dropped by idempotency no-op).
- **M-4 (P1) Re-tag `usage_events`** — add `workflow_run_id`/`campaign_key`/`institution_group_id`; **without this,
  per-campaign spend is impossible even once rollups exist.**
- **M-5 (P2) `usage_budgets` + dashboards** (read side; note: budget *caps* are dropped, but budget *visibility* is not).

### Plan 12 — Compliance & Consent (~60%)
- **CO-1 (P2) Named `ConsentService`/`SuppressionService`** — logic currently lives in the gate + `SmsComplianceService`.
- **CO-2 (P2) US cross-timezone quiet hours** — clinic-TZ only; a US clinic calling out-of-region patients can breach their local quiet hours.
- *(Consent-basis hard-enforcement for marketing voice = V-3; DNC admin endpoint = U-2b.)*

---

## Suggested execution order
1. ✅ **P0 send-safety bundle** + **Plan 12 semantic layer** — DONE.
2. ✅ **XC-1 send-time idempotency (all channels)** — DONE (+ latent hold-resume collision fixed).
   Remaining: **XC-1b** crash-window (committed-before-send claim / provider idempotency key) before high volume.
3. **Plan 11 rollups + voice metering + re-tagging** (M-1..M-4) — unblocks Plan 08 analytics.
4. **Plan 09 resilient core** (D-1..D-4) — reschedule re-enroll, freshness window, projections/backfill/reconciliation.
5. **Plan 05 email hardening** (E-1/E-2) — unsubscribe + bounce/complaint; independent, legal minimum.
6. **Plan 06 differentiators** (C-1..C-3) — PMS write-back (fixes dead confirm-branch), Sales Qualification.
7. ✅ **Plan 07 AI callback** — core v1 merged (2026-07-04). Remaining: CB-2/CB-3 confirmations + CB-4 template/tables.
8. **Plan 03 outcome feedback loop** (V-1..V-3) + **Plan 10** (PR-1 quick audit fix, then PR-2/PR-3).
9. **Plan 08 full UI** (U-1..U-5) + **Ops** (XC-4) + remaining P2 polish alongside.
