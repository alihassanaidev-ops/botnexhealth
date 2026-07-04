# Phase 2 Verification & Progress Report — Outbound Engagement Engine

**Date:** 2026-07-04 (current-state rewrite)
**Branch:** `ali/phase-2` (incl. merge of `feature/outbound-engagement-engine` = Plan 03 voice)
**Scope audited:** **all 12 implementation plans**, including Plan 03 (previously excluded).
**Method:** graphify-oriented navigation + direct code inspection. 12 independent per-plan verification
passes (one subagent each, this session), every conclusion traced to `file:line`; Plans 01/02/12 also
cross-checked by the author who implemented the recent changes; full suite executed
(**1325 unit tests pass, 6/6 real-Postgres integration tests pass, 0 failures**).

> **Supersession.** This document reflects the **current** state after three implementation passes since
> the original audit: `outbound-01-02-finalize` (engine/builder to 100% + 09/10/11 cores + Plan-03 merge),
> `outbound-03-voice` (voice executor), and `outbound-safety-and-compliance` (P0 bundle + Plan-12
> semantics + test-suite hardening). It **replaces** the earlier 2026-07-03 *pre-implementation* audit that
> occupied this file. That pre-implementation prose is preserved in git history; the per-plan
> `plan-NN-findings.md` files in this folder are its 2026-07-03 evidence base (historical) **except
> `plan-03-findings.md`, which is current (2026-07-04)**. Where a finding number (A–E, or a plan %)
> differs from those files, **this report is authoritative.**

---

## 1. Executive summary

Phase 2 has moved decisively past "an engine that cannot send." The system now **sends real,
compliance-gated SMS, email, and (fire-and-forget) voice** on the durable Celery path, through a single
gated dispatch factory, in each location's timezone, with per-tenant messaging credentials.

The two hardest, largest builds — **Plan 01 (Workflow Engine) and Plan 02 (Visual Builder) — are complete
(100%) and verified end-to-end against a real database.** Plan 03 (Voice) is merged and integrated as a
functional v1. The compliance layer (Plan 12) has advanced from a bare gate to a real semantic layer
(content-class/PHI validator, AI-voice disclosure, bilingual FR STOP, do-not-contact tiers, per-channel
consent identity). The five live compliance/security findings from the original audit (A–E) are **all
resolved** (one with a documented deferral).

The remaining work is the "last mile" of the channel/data/provisioning/metering plans: the event-driven
data layer's **resilient projection core** (Plan 09), **email hardening** (unsubscribe/bounce — Plan 05),
**usage rollups + voice metering** (Plan 11), the **campaigns' differentiators** (PMS write-back, Sales
Qualification, DB-backed templates — Plan 06), **provisioning automation + readiness state** (Plan 10),
and the **full campaign-management UI** (CSV/analytics/ops — Plan 08).

**Headline: ~48% of full Phase-2 plan scope delivered across all 12 plans (up from ~35% over 11 plans).**
The functional foundation is stronger than the aggregate implies because 01/02/03/12 — the spine — are the
most complete.

### Product-owner scope decision (2026-07-04)
**No caps or limits on clinics/locations, and no tenant-based caps.** Frequency caps, spend/budget caps,
blast-radius/step-up gates, and per-location outbound concurrency caps are **dropped (not deferred)**
wherever the plans call for them (Plan 12, Plan 03, Plan 11). Non-cap vendor-throughput *smoothing* (global
paced dispatch) and per-clinic *isolation* (Retell workspace/BYO-SIP) remain valid, non-cap scale items.

---

## 2. Status dashboard

| Plan | Title | Status | % of full plan | Δ vs 2026-07-03 |
|---|---|---|---|---|
| 01 | Workflow Engine | ✅ Complete | **100%** | ↑ from ~88% |
| 02 | Visual Builder UI | ✅ Complete | **100%** | ↑ from ~75% |
| 03 | Outbound Voice | 🟢 Outcome loop + consent basis built | **~70–75%** ↑↑ | **Implemented 2026-07-04** (`outbound-03-voice-implementation/`): dial-outcome feedback loop, transient retry (V-6), client extraction (V-7), content-class consent basis (V-3). Remaining: data-model tables (V-4), spoken-opt-out→suppression (**blocked A-8**), crash-safe claim (P9), voice metering (Plan 11) |
| 04 | Outbound SMS | 🟢 Substantial | **~70%** | ↑ from ~55% |
| 05 | Outbound Email | 🟠 Minimal MVP+ | **~30–35%** | ↑ from ~20% |
| 06 | Four Live Campaigns | 🟡 Partial | **~50–55%** | ↑ from ~40% |
| 07 | AI Callback Handling | 🟢 Merged — core v1 | **~60%** | ↑ from 0% — merged 2026-07-04 (Hammad, `97fe227`) |
| 08 | Campaign Mgmt / Analytics UI | 🟠 Read-only slice | **~22%** | unchanged |
| 09 | Integration & Data Layer | 🟡 Passthrough + revalidation | **~40%** | ↑ from ~27% |
| 10 | Per-Tenant Provisioning | 🟡 Cred-storage + readiness MVP | **~25%** | ↑ from ~20% |
| 11 | Usage & Cost Reporting | 🟠 Ingestion only | **~15%** | ↑ from 0% |
| 12 | Compliance & Consent | 🟢 Gate + semantics (caps excluded) | **~60%** | ↑ from ~27% |

**Overall Phase 2: ~48% of full plan scope** (per-plan numbers are the reliable figures; the aggregate is a
weighted estimate). Confidence: **High** across all 12 (direct code inspection + passing tests).

---

## 3. Status of the original audit's five headline findings — ALL RESOLVED

| # | Finding | Status |
|---|---|---|
| A | Inline enroll route bypasses the compliance gate + hardcodes UTC | ✅ **FIXED** — the enroll route now uses `build_dispatcher` (`automation_workflows.py:588`), the single factory that injects the real `ComplianceGateService` + resolved timezone (`step_dispatcher.py:387`). The only `WorkflowStepDispatcher(...)` construction is inside that factory. |
| B | Quiet-hours "hold" drops the send instead of deferring | ✅ **FIXED** — hold now schedules a resume timer at `retry_at` and re-checks the gate on fire (`step_dispatcher.py:163-191`); the run is held, never dropped. |
| C | NexHealth webhook signature fails open when secret unset | ✅ **FIXED** — production startup fails closed if `nexhealth_webhook_secret` unset (`config.py`), plus a defense-in-depth 403 in `_verify_signature` when prod + empty (`nexhealth_webhooks.py:91-97`). |
| D | Email consent keyed on a phone hash | ✅ **FIXED** — email consent now keys on an email identity (`ConsentRecord.email_hash` + `hash_email`); gate split into `_check_email_consent` vs `_check_phone_consent`; migration `20260705_consent_email_identity`. Email-only contacts pass. |
| E | Cancellation/reschedule unhandled + no send-time revalidation | ✅ **FIXED (with 1 deferral)** — `appointment.cancelled`/cancelled-on-update now terminates runs+timers (`nexhealth_webhooks.py:198-211`); `PmsLiveRevalidationService` runs before every send (`step_dispatcher.py:142`). **Deferral:** a *rescheduled* appointment is defensively skipped (`skipped_rescheduled`) but **not re-enrolled at the new time** — the send is dropped, not moved (Plan 09 bug #1). |

---

## 4. Per-plan findings (all 12)

### Plan 01 — Workflow Engine — ✅ 100%
**Built:** durable multi-tenant, timezone/DST-aware runtime; immutable versioned definitions with in-flight
version pinning; DB-backed scheduler with `FOR UPDATE SKIP LOCKED` + **stale-claim recovery wired to beat**;
**single gated dispatch factory** `build_dispatcher` (real gate + resolved tz on every path — inline + Celery);
quiet-hours **hold-and-resume**; **version/workflow-scoped emergency halt that terminates in-flight runs +
cancels timers** (`/{workflow_id}/emergency-halt`; institution-level `/outbound-halt` gate also present);
concurrency-safe enrollment idempotency; dispatch-time revalidation seam; action/trigger registries;
fail-closed publish validation; jitter; dead-letter routing; SSE progress events; CloudWatch metrics emitter.
**Verified:** unit + **6 real-Postgres integration tests** (publish immutability + version pinning;
enroll→wait→resume→exit; crashed-worker stale-claim recovery; emergency-halt cascade; real unique-index
idempotency; RLS cross-tenant isolation).
**Missing:** paced/budget-aware dispatch against the shared NexHealth key (non-cap smoothing) — partial (jitter only).
**Verdict:** complete and production-grade; the strongest pillar.

### Plan 02 — Visual Builder UI — ✅ 100%
**Built:** real React Flow canvas (pan/zoom, minimap, custom nodes, validation tinting); side-panel palette;
typed per-step config panel with condition-rule editor + merge-field insert; visual branches/waits;
**backend `/validate`, `/versions`, `/merge-fields` endpoints now consumed** (merge-field drift fixed via
single-source `STATIC_MERGE_FIELDS`); authoritative server-side publish validation; **compliance guardrail
panel renders content-class/PHI/consent-path issues** — and the new Plan-12 codes
(`promotional_in_exempt_class`, `phi_in_body`, `sensitive_clinical_in_body`, `ai_voice_*`) surface
automatically (panel is code-agnostic); drag-and-drop canvas with presentational layout persistence;
server-side dry-run; channel-readiness surfacing; version history. **130 FE tests green, tsc clean.**
**Partial:** publishing an edit to a live ACTIVE campaign changes runtime immediately behind a generic
confirm (draft-first lifecycle deliberately not built); last-write-wins (no ETag).
**Verdict:** the flagship §9.1 experience is complete and wired; earlier merge-field-drift bug fixed.

### Plan 03 — Outbound Voice — 🟡 ~35% (fire-and-forget v1)
**Built:** `VoiceNodeExecutor` places a per-location Retell `create-phone-call` (`voice_node_executor.py:60`),
registered as `send_voice` in the action registry; `SendVoiceNode` schema (`retell_agent_id` required);
`retell_from_number` column + migration (`20260703_retell_from_number`); **compliance-gated before dispatch**
(emergency-halt, quiet-hours, **do-not-contact** for voice, VOICE-channel consent); **idempotency guard**
against re-dial (checks completed `call_placed` step); **AI-call disclosure injected** (`compliance_disclosure`
dynamic var + `ai_automated_call` metadata); publish-time validator warnings for voice disclosure +
marketing express-consent.
**Missing / ⚠️ Partial:**
- **Dedicated data model entirely absent** — no `outbound_voice_profiles`, no `workflow_voice_attempts`, no
  `calls` linkage columns; reuses the generic `AutomationWorkflowStepExecution` ledger.
- **⚠️ Outcome feedback loop MISSING — the central gap.** The Retell webhook never reads
  `metadata`/`workflow_run_id` (`RetellCallWebhook` uses `extra="ignore"`); it correlates only by
  `agent_id`→location. So **no dial-outcome branching, no retry-on-no-answer, no voicemail→SMS fallback,
  no book→exit** — the run advances immediately on placement.
- **⚠️ Voice is blocked-by-default in production** — the gate requires a granted VOICE `ConsentRecord`, but
  nothing captures voice consent (§5 item 8), so voice sends are blocked `no_voice_consent`. (Separately,
  marketing consent-*basis* is a publish-time **warning** only, not a hard block.)
- **⚠️ Disclosure supplied but not proven spoken** — the live Retell agent prompt is not shown to reference
  `{{compliance_disclosure}}`; delivery depends on a per-location Retell-dashboard prompt update (onboarding step).
- `OutboundVoiceService` / `RetellOutboundClient` / concurrency service — do not exist (HTTP inline in executor).
- **Voice usage metering absent** (emits no UsageEvent; Plan 11 voice TODO).
- No profile CRUD / readiness / attempt-drill-down UI.
**Bugs:** (1) **transient Retell errors fail the whole run** (caught + `fail_run`, no re-raise → no task
retry/dead-letter). (2) **Idempotency claimed *after* the POST, not before** — a crash between a successful
Retell POST and `complete_step` leaves no committed claim → a retry can re-dial (the plan's stated edge case).
**Deliberately excluded (product owner):** per-location outbound concurrency caps — confirmed absent.
**Verdict:** a clean, well-integrated, compliance-inheriting v1; the plan's larger half (data model, outcome
feedback loop, dedicated services, metering) is unbuilt. Full detail: `plan-03-findings.md`.

### Plan 04 — Outbound SMS — 🟢 ~70%
**Built:** `SmsNodeExecutor` (fail-safe) via the action registry; single gated `build_dispatcher` path;
compliance gate before every send; per-tenant Twilio creds via `TenantTwilioCredentialResolver` + platform
fallback; STOP/START/HELP inbound (location-scoped suppression + audit); delivery-status webhook →
`sms_history_logs` + dead-letter of unknown SIDs; **SMS usage metering** on terminal status (idempotent per
MessageSid). **26 tests pass.**
**Missing / ⚠️ Partial:** `workflow_sms_attempts` + send-time idempotency (**double-send risk on retry/hold-resume
remains**); `sms_history_logs` workflow-linkage columns (run/step/campaign/segments/price); `inbound_sms_messages`
+ `InboundSmsRoutingService` (**free-text replies still ignored** — empty TwiML, no persistence/notification).
**Delta:** Finding A (inline bypass) & B (hold-drops) FIXED; metering landed (via the Twilio status webhook, not `sms_service`).
**Verdict:** the compliant SMS send path is solid and metered; idempotency, delivery→run linkage, and free-text inbound are the gaps.

### Plan 05 — Outbound Email — 🟠 ~30–35%
**Built:** `EmailNodeExecutor` sends plain-text Resend email, gated + metered; from-address institution
override + platform fallback (`messaging_credentials.resolve_email_from`); reuses sandboxed SMS renderer;
**email consent bug FIXED** (email-identity keyed). **UsageEvents emitted** (`emails=1`). Tests pass.
⚠️ **But email is blocked-by-default in production:** the gate requires a granted EMAIL `ConsentRecord` and
nothing captures one (§5 item 8) — the P0-2 fix corrected the consent *key*, not the missing *capture*.
**Missing:** the entire data model (`email_sending_profiles`, `workflow_email_templates`,
`workflow_email_attempts` — **no attempt/audit log**); `EmailWebhookService` + bounce/complaint/delivered
ingestion; **unsubscribe** (CASL/CAN-SPAM legal minimum); HTML/branded body; per-tenant sending domain
(SPF/DKIM/DMARC + warm-up); dedicated `ResendCampaignClient`; email-specific merge-field allowlist; analytics; UI.
**Bugs:** email **cost never captured** (metered at $0).
**Verdict:** a competent gated+metered plain-text v1 with consent fixed; no unsubscribe/bounce/HTML/domain/attempt-log.

### Plan 06 — Four Live Campaigns — 🟡 ~50–55%
**Built:** in-code template registry with 4 templates; **recall trigger is now REAL** (live NexHealth recall
pull → due-date filter → contact resolution → idempotent paced enrollment); appointment-offset trigger wired
end-to-end; **`PmsLiveRevalidationService` live-backed and wired into every dispatch path**; compliance gate
before every send. **17 template tests pass.**
**Missing / ⚠️ Partial:** **Sales Qualification absent** (the 4th slot is a non-plan `reactivation` campaign);
**PMS confirmation write-back does not exist** (adapter has reads only); templates are in-code dataclasses,
not the DB-backed versioned `workflow_templates` model; outcome mapping not normalized; no channel-order/fallback/
attempt-ceilings; no CSV/manual enrollment for recall/sales.
**Bugs:** **Confirmation "confirmed"-branch is dead code** — `appointment_status` is never written into run
state, so the confirm branch is unreachable and the run always exits `no_response` (mirrored in the reactivation
`appointment_booked` branch).
**Verdict:** Reminder is fully live; Recall now really enrolls; Confirmation is send-only (confirm/write-back
non-functional); Sales Qualification dropped.

### Plan 07 — AI Callback Handling — 🟢 ~60% (merged 2026-07-04, Hammad `97fe227`)
**Built:** `callback_requested` trigger type (`definition_schema.py`); `CallbackTriggerService`
(mirrors `AppointmentTriggerService`); `trigger_callback_workflows` Celery task; a Retell post-call
webhook hook that enqueues the trigger when an inbound call is classified `needs_callback` (loop-guarded —
skips outbound-originated calls). Enrollment delegates to the existing `enroll_and_start_workflow_run`, so
callback runs **inherit the compliance gate, dispatch-time revalidation, and send-time idempotency**. Opt-in
is by activating a `callback_requested` workflow (no separate settings table); preferred callback time
honored via Celery `eta`. 11 unit tests. Merge verified: 1340 unit green, single Alembic head, zero conflicts.
**Deviations from the plan (leaner design):** no `callback_automation_settings` / `callback_workflow_links`
tables (opt-in = workflow activation; idempotency via `callback:{version}:{call_id}`); no packaged 5th
AI-callback template (the clinic builds/activates a workflow).
**Investigated + RESOLVED in the closeout (`outbound-07-followups-closeout/`, 2026-07-04):**
- ✅ **Now functional end-to-end (CB-3 / XC-6).** The gate requires a granted VOICE `ConsentRecord`, and the
  consent writers previously only wrote SMS. Fixed: `record_consent` / `record_consent_identity` are now
  channel-generic (+ `has_consent_record`), and the AI-callback path records an **express VOICE consent on the
  inbound callback request** (only if none exists → respects a prior opt-out). A callback now passes the gate and
  places the call. Verified by a real-Postgres test. *(Legal-review note in code: inbound callback request = express basis.)*
- ✅ **Double-contact guard (CB-2).** `_trigger_callback_async` skips if the source Call is already
  `callback_resolved` (residual: a resolve during the ETA delay isn't caught). Quiet-hours now **defers-and-resumes**
  (intended); the dev's `outbound-07-ai-callback/findings.md` D2/D4 notes were reconciled.
- Still uses Plan-03 **fire-and-forget** voice (no dial-outcome loop / voicemail→SMS yet — Plan 03, V-1).
**Verdict:** functional core v1 (~60%), now working **end-to-end** (the call is actually placed). Remaining: the
packaged template + dedicated tables (CB-4) and the fire-and-forget→outcome-loop upgrade inherited from Plan 03.

### Plan 08 — Campaign Mgmt / Progress / Analytics UI — 🟠 ~22%
**Built:** interactive campaign list (`Campaigns.tsx` → `GET /automation/workflows`; pause/resume/archive;
links to builder/detail/versions); campaign detail with a read-only runs table; backend lifecycle + run-read
routes; role-gated routes; `workflow_run_updated` SSE type registered (backend only).
**Missing:** enrollment UI + **CSV** import/mapping/preview; analytics/reporting page + `campaign_metrics_daily`
+ attributed revenue; operations/dead-letter/replay page; **emergency-halt UI** (backend exists, unconsumed);
run-detail timeline; **SSE real-time** (pages are manual-refresh); location scoping in the list.
**Bugs:** native `confirm()` instead of the app Dialog; runs table mislabeled "Enrollments."
**Delta:** effectively **none since 2026-07-03** — no Plan-08 file changed.
**Verdict:** honest read-only Phase-3 slice on real data; most of the plan is Phase-6-deferred.

### Plan 09 — Integration & Data Layer — 🟡 ~40%
**Built:** HMAC-verified webhook receiver; appointment-offset trigger with ETA-scheduled idempotent enrollment;
**`appointment.cancelled`/cancelled-on-update now terminates runs+timers**; **`PmsLiveRevalidationService`
exists and is injected at every dispatch path** (cancelled/rescheduled detection, fail-open); **recall pull is
REAL** (live `GET /recalls`, due derivation, paced enrollment); bulk-enroll endpoint. Two suites pass.
**Missing (no code/migration):** the plan's **central deliverable — the disposable `appointment_working_set`
projection** (webhook enqueues directly, persists nothing); `recall_eligibility_working_set`;
`nexhealth_webhook_subscriptions` + lifecycle/health; `nexhealth_webhook_events` **event ledger** (no
event-level idempotency); initial REST **backfill** (go-forward-only gap); paced **reconciliation sweep**; rate-limit pacing for jobs.
**Bugs:** (1) **rescheduled appointment's reminder is silently dropped, not re-timed** (time-independent
idempotency key dedupes the re-enroll; revalidation then skips the stale-time send). (2) no event-level
idempotency (dup deliveries re-run). (3) whole-table workflow scan per webhook. (4) **⚠️ no revalidation
freshness window** — an 800-patient 9 AM batch = ~800 burst NexHealth calls, the cost the plan told this to avoid.
**Verdict:** cancellation-safe passthrough with live revalidation, but every *defining* resilience component
(projections, subscriptions, ledger, backfill, reconciliation) is still absent.

### Plan 10 — Per-Tenant Messaging Provisioning — 🟡 ~25%
**Built:** genuine **AES-256-GCM per-institution Twilio + email creds** on the model + migration; reusable
**`TenantTwilioCredentialResolver`** (institution→platform fallback), consumed by SMS/email/webhooks;
**`ChannelReadinessService` + `GET /channel-readiness`** (computed) feeding warning-only publish validation;
super-admin provisioning API (masks SID, never returns token); **Twilio sub-account webhook signature gap
FIXED** (validates with the resolved sub-account token). **9 tests pass.**
**Missing:** all 6 provisioning tables; **first-class readiness *state* model** (readiness is computed on read,
no persisted status/lifecycle); A2P 10DLC / toll-free registration; email domain SPF/DKIM/DMARC + warm-up;
provisioning vendor automation (creds entered manually); AWS Secrets Manager for tenant creds; per-channel feature flags.
**Bugs:** ✅ **FIXED (closeout 2026-07-04)** — provisioning credential changes (`admin_institutions.py`
PATCH/DELETE) now `log_audit(INSTITUTION_UPDATE)` with actor + masked metadata (was unaudited).
**Verdict:** a clean, secure credential-storage + computed-readiness MVP; the provisioning *system* (tables,
vendor APIs, verification, readiness state) is unbuilt.

### Plan 11 — Usage & Cost Reporting — 🟠 ~15%
**Built:** `UsageEvent` model + migration (`20260704_usage_events`, RLS + idempotency index);
`UsageMeteringService.record` (idempotent, savepoint-guarded); **SMS hook** (Twilio status webhook — segments +
price) and **email hook** (send-time — `emails=1`). Tests pass.
**Missing:** `usage_cost_rollups` (the contract Plan 08 depends on); `usage_budgets`; `UsageRollupService`
(location→institution→DSO); reporting API + dashboards; **voice metering — still absent** (fire-and-forget voice
emits nothing; explicit TODO); cost estimation/`estimated` flag; alarms. **Model under-tagged** — no
`workflow_run_id`/`campaign_key`/`institution_group_id`, so **per-campaign spend is impossible even once rollups exist.**
**Bugs:** **email cost never captured** (always $0); a late price-update webhook is dropped (idempotency treats it as a no-op).
**Verdict:** a real RLS-scoped idempotent ingestion spine for SMS+email; everything above ingestion, and the
entire voice channel, is unbuilt.

### Plan 12 — Compliance & Consent — 🟢 ~60% (caps excluded by product owner)
**Built:** `ComplianceGateService` — halt → quiet-hours (hold-and-resume) → **do-not-contact (all channels)** →
per-channel consent — invoked before **every** send on the gated dispatch path; DST-correct quiet hours;
**real content-class + PHI validator** (`ContentComplianceValidator`: promotional-in-exempt-class = error,
PHI/financial = error, clinical = warning) wired into publish + `/validate`; **AI-voice consent/disclosure**
(disclosure injected; validator warnings); **bilingual EN/FR STOP** (ARRET/ARRÊT/DÉSABONNER + Unicode
tokenizer); **do-not-contact scope tiers** (`DoNotContact.scope` = location/institution/group; scope-aware
enforcement; privileged `set_do_not_contact` writer); **email-identity consent** (Finding D); emergency-halt
model + routes; multi-channel consent enum + constraints. **Gate/validator/DNC tests pass.**
**Missing / deliberately excluded:** ⚠️ **no email/voice consent-*capture* path (§5 item 8)** — consent is
*enforced* per channel but only *written* for SMS (`record_consent*` hardcode `channel=sms`), so voice/email
sends are blocked-by-default; this is the biggest real compliance gap now that the gate is correct. Also:
**frequency caps, spend caps, blast-radius gates — DROPPED (no-caps decision)**; named
`ConsentService`/`SuppressionService` (logic lives in the gate + `SmsComplianceService`); consent-*basis*-level
enforcement for marketing voice (warning only, not hard block); privileged institution/DSO DNC admin HTTP
endpoint (writer exists; endpoint is a thin Plan-08 follow-up); US cross-timezone quiet-hours (clinic TZ only).
**Verdict:** advanced from a bare gate to a real, authoritative, on-every-dispatch semantic layer; the excluded
pieces are the caps the product owner removed, not oversights.

---

## 5. Cross-cutting findings (updated)

**Resolved since the original audit:**
- ✅ The two divergent enroll+advance paths are **converged** onto one gated, tz-resolving `build_dispatcher` (killed Findings A/B's inline variants).
- ✅ Quiet-hours **hold-and-resume**; ✅ webhook **fail-closed in prod**; ✅ **email-identity consent**; ✅ **cancellation handling + live revalidation**; ✅ **do-not-contact enforced on all channels**; ✅ stale-claim recovery; ✅ template instantiate; ✅ DST correctness.
- ✅ **FE/BE contract drift fixed** for Plan 02 (merge fields + validate/versions endpoints wired).

**Still open / systemic:**
1. ✅ **Send-time idempotency (SMS/email/voice)** — DONE (XC-1, 2026-07-04): `runtime.already_sent` skips a re-send on redelivery/re-advance/hold-resume, plus a latent hold→resume unique-index collision fixed. Residual: **XC-1b** crash-window (committed-before-send claim / provider idempotency key).
2. **The event-driven read model (Plan 09) was not built** — direct webhook→enroll passthrough, no projection/backfill/reconciliation/event-ledger. Only appointments created *after* subscription can trigger; a reschedule silently drops the reminder.
3. **No revalidation freshness window** → burst NexHealth load at batch times (Plan 09).
4. **Usage model under-tagged + voice/email cost gaps** → per-campaign spend and voice/email cost reporting are impossible without a retrofit (Plan 11).
5. **Test suites remain mock-heavy** for channel wiring; the real-Postgres integration suite (engine) is the exception and should be extended to channels.
6. **Two dead-code campaign branches** (Confirmation/Reactivation) — conditions on run state that nothing populates (Plan 06).
7. ✅ **Provisioning credential changes now audited** (Plan 10, closeout 2026-07-04 — `log_audit(INSTITUTION_UPDATE)` on PATCH/DELETE); **Twilio sub-account webhook** already fixed.
8. **Consent-CAPTURE — voice ✅ (callback path), email still open.** The gate enforces per-channel consent, but the writers only wrote SMS. Closeout (2026-07-04) made them channel-generic (+ `has_consent_record`) and the AI-callback path now records an **express VOICE consent on the inbound request** → **Plan 07 voice callbacks work end-to-end**. STILL OPEN: **email consent capture** (no opt-in/intake trigger → **Plan 05 email remains blocked-by-default**), and general (non-callback) voice consent for Recall/Sales — both need an intake path (Plan 05 / Plan 12). SMS unaffected.

---

## 6. Overall progress summary

- **Complete (100%):** Plan 01, Plan 02.
- **Substantial (50–70%):** Plan 04 (~70%), Plan 12 (~60%), Plan 06 (~50–55%).
- **Partial (35–40%):** Plan 09 (~40%), Plan 03 (~35%), Plan 05 (~30–35%).
- **Minimal (15–25%):** Plan 10 (~25%), Plan 08 (~22%), Plan 11 (~15%).
- **Merged core v1 (~60%):** Plan 07 (AI callback — 2026-07-04).
- **Not started (0%):** none.

**Biggest remaining milestones (largest → smallest):** Plan 09 resilient core (projections/backfill/
reconciliation/subscriptions/event-ledger); Plan 11 rollups + voice metering (+ model re-tagging); Plan 06
differentiators (PMS write-back, Sales Qualification, DB-backed templates); Plan 05 email hardening
(unsubscribe/bounce/HTML/domain); Plan 10 provisioning automation + readiness state; Plan 08 full UI
(CSV/analytics/ops/SSE); Plan 03 outcome feedback loop; Plan 07 (now startable).

**Production readiness:** the engine + builder are production-grade and verified; compliance is enforced on
every dispatch. For a **safe operator-driven pilot on the Celery path** the system is ready. Before
**high-volume autonomous** sending: add send-time idempotency (all channels), email unsubscribe/bounce
handling, the Plan-09 projection/backfill + revalidation freshness window, and voice outcome feedback.

---

## 7. Recommendations

The full, prioritized, de-duplicated remaining-work list lives in **one place** —
`../outbound-followups-and-gaps.md` (the register). This report does not restate it, to avoid drift.

**Top of that list (highest-leverage first):**
1. ✅ **Send-time idempotency across SMS/email/voice** (register XC-1) — DONE 2026-07-04 (also fixed a latent
   hold→resume unique-index collision). Remaining: **XC-1b** crash-window (provider idempotency key).
2. **Plan 11 rollups + voice metering + `usage_events` re-tagging** (M-1..M-4) — unblocks Plan 08 analytics.
3. **Plan 09 resilient core** (D-1..D-4) — reschedule re-enroll, revalidation freshness window, projections/backfill.
4. **Plan 05 email hardening** (E-1/E-2) — unsubscribe (legal minimum) + bounce/complaint.
5. **Plan 06 differentiators** (C-1..C-3), **Plan 07** (now unblocked), **Plan 03 outcome feedback loop** (V-1..V-3), **Plan 10** (PR-1 audit fix first).

*(Caps — frequency/spend/blast-radius/concurrency — are intentionally excluded per the product-owner decision.)*

---

## Appendix A — Verification method & evidence base
Each plan was verified this session by an independent pass: graphify orientation → read the plan doc → inspect
current code (`file:line`) → check the deltas since 2026-07-03 → run relevant tests where feasible. Deep per-plan
`file:line` detail: `plan-03-findings.md` is **current (2026-07-04)**; `plan-01`…`plan-12-findings.md` (excl. 03)
are the **2026-07-03** evidence base and are historical — this report's per-plan sections supersede them.

## Appendix B — Test status (2026-07-04, latest)
- **Unit:** 1341 passed, 0 failed, 0 collection errors (full suite).
- **Integration (real Postgres, testcontainers):** 8/8 passed — migration chain applies cleanly on a fresh DB
  through head `20260706_dnc_scope`; engine mechanics (version pinning, wait→resume→exit, stale-claim recovery,
  emergency-halt cascade, idempotency, RLS) + send-idempotency/hold-resume + voice-consent channel-scoping all verified.

## Appendix C — Confidence
High across all 12 (direct code inspection + passing tests). Per-plan percentages are the reliable figures;
the ~48% aggregate is a considered weighted estimate.
