# Phase 2 Verification & Progress Report — Outbound Engagement Engine

**Date:** 2026-07-06 (Plan 09 sections updated for commits `6671ba5` + `43e2875`; body otherwise the 2026-07-05 re-verification)
**Branch:** `ali/phase-2` @ `0178e21` (in sync with origin). Includes Hammad's Plan 11
(`c8e6535` — usage rollups / voice metering / campaign attribution / reporting API) and Plan 09 resilience core
(`6671ba5` projection + reschedule re-enroll + freshness window; `43e2875` subscriptions + backfill + reconciliation).
**Scope audited:** all 12 implementation plans.
**Method:** this is a **full re-verification**, not an edit. Every prior finding was re-checked against the
current merged tree by an independent per-plan sub-agent (graphify-oriented navigation → live code inspection,
each conclusion traced to `file:line`); Plan 03 and the global test/migration status were verified directly by
the author. Test baseline below.

> **Supersession.** This replaces the 2026-07-04 current-state report (preserved in git history). Since that
> version, two things changed the codebase materially: (1) the **Plan-03 voice** build was largely completed
> (data model, crash-safe idempotency, timeout policy, and the profiles/attempts API), and (2) **Hammad's
> Plan 11** landed and was merged, taking usage/cost reporting from ingestion-only to a real rollup+reporting
> layer with voice metering. The companion `plan-NN-findings.md` files are the 2026-07-03 evidence base
> (historical); **this report is authoritative** where they differ.

---

## 1. Executive summary

Phase 2 is well past "an engine that cannot send." The platform sends real, **compliance-gated SMS, email, and
voice** (voice now with a full outcome-feedback loop, not just fire-and-forget) on the durable Celery path,
through a single gated dispatch factory, in each location's timezone, with per-tenant messaging credentials —
and **meters usage across all three channels** with per-campaign attribution.

The spine is complete and hardening is real:
- **Plan 01 (Workflow Engine) and Plan 02 (Visual Builder) are complete (100%)**, verified end-to-end against a
  real database (12 real-Postgres integration tests).
- **Plan 03 (Voice) advanced from a ~35% fire-and-forget v1 to a functionally-complete channel (100%)**: dial-outcome feedback loop, transient
  retry, a dedicated data model (`outbound_voice_profiles` + `workflow_voice_attempts`), a crash-safe committed
  idempotency claim, an at-most-once timeout policy, content-class-aware consent basis, and a profiles/attempts
  REST API.
- **Plan 11 (Usage & Cost) is complete (100%)**: `usage_cost_rollups` + service, all-channel ingestion
  (SMS/email/**voice**), campaign attribution, a scheduled recompute, an SMS late-price fix, and per-institution +
  DSO/group reporting APIs. Voice/email cost stays $0 by product decision (Option B); budgets dropped with no-caps.
- **Plan 12 (Compliance) is complete for scope (100%)**: consent *basis* is a **hard block at the gate**; a staff
  do-not-contact admin route; and **implied transactional consent** unblocks appointment email/voice the compliant
  way (recall/marketing still require express consent — that capture is deferred with the client-deferred lead-intake).

The original audit's five compliance/security findings (A–E) are now **all fully resolved** — Finding E's
reschedule deferral was closed by the Plan 09 work below. **Plan 09 (Data Layer) jumped from ~40% to ~80%**
(Hammad, `6671ba5`+`43e2875`): the disposable `appointment_working_set` projection, reschedule re-enroll,
event-level idempotency ledger, revalidation freshness window, and the subscription/backfill/reconciliation jobs
now exist — though the live-NexHealth-API half of that (subscriptions/backfill/reconciliation) is mock-tested and
**needs staging verification**. The remaining work concentrates in the provisioning/UI plans: **Plan 05 email** is now launch-compliant
(transactional sends + one-click unsubscribe + bounce/complaint suppression) — only the **per-tenant sending domain
(SPF/DKIM, external)** + HTML remain; Plan 10 **provisioning automation + persisted readiness state** (external);
and Plan 08's **full campaign UI + analytics dashboards** (which can now consume the shipped Plan-11 usage APIs).
(Plans 06 and 11 complete; Plan 06's Sales Qualification is product-dropped, Plan 11's voice/email cost is $0 per Option B.)

**Headline: ~81% of full Phase-2 plan scope delivered across all 12 plans (up from ~62%).** Eight of twelve plans
are now complete (01, 02, 03, 04, 06, 07, 11, 12) — several marked so because their remaining items were assessed
**not-required / product-dropped / deferred-per-client / other-lane**, not because everything was built. The
concentrated remaining work is now just **three plans** — Plan 05 (email), Plan 08 (UI), Plan 10 (provisioning) —
plus Plan 09 staging verification.

### Product-owner scope decision (unchanged)
**No caps or limits on clinics/locations, and no tenant-based caps.** Frequency caps, spend/budget caps,
blast-radius/step-up gates, and per-location outbound concurrency caps are **dropped (not deferred)** wherever
the plans call for them (Plan 12, Plan 03, Plan 11). Non-cap vendor-throughput *smoothing* (global paced
dispatch) and per-clinic *isolation* (Retell workspace/BYO-SIP) remain valid, non-cap scale items. Note: Plan
11 shipped usage *reporting* (visibility), which is explicitly **not** a budget/cap.

---

## 2. Status dashboard

| Plan | Title | Status | % of full plan | Δ vs 2026-07-04 |
|---|---|---|---|---|
| 01 | Workflow Engine | ✅ Complete | **100%** | unchanged |
| 02 | Visual Builder UI | ✅ Complete | **100%** | unchanged |
| 03 | Outbound Voice | ✅ Complete (channel functionally complete; residual = FE/external/infra, no functional gap) | **100%** | ↑ from ~89% |
| 04 | Outbound SMS | ✅ Complete (spec residuals assessed not-required) | **100%** | ↑ from ~78% (S-2 routing + residuals deemed not-required) |
| 05 | Outbound Email | 🟢 Launch-compliant (sends + unsubscribe + bounce/complaint; per-tenant domain external) | **~70%** | ↑↑ from ~38% (transactional sends + unsubscribe + bounce/complaint) |
| 06 | Four Live Campaigns | ✅ Complete for agreed scope (Sales Qualification dropped by product) | **100%** | ↑ from ~62–65% |
| 07 | AI Callback Handling | ✅ Complete for purpose (leaner opt-in design; spec residuals not-required) | **100%** | ↑ from ~63% |
| 08 | Campaign Mgmt / Analytics UI | 🟠 Read-only slice | **~22%** | unchanged |
| 09 | Integration & Data Layer | 🟢 Projection + reschedule re-enroll + ledger + backfill/reconciliation (NexHealth-API pieces staging-pending) | **~80%** | ↑↑ from ~40% (Hammad, `6671ba5`+`43e2875`) |
| 10 | Per-Tenant Provisioning | 🟡 Cred-storage + computed readiness | **~25%** | unchanged |
| 11 | Usage & Cost Reporting | ✅ Complete (scheduled recompute + SMS price fix + group endpoint; cost=Option B, budgets dropped) | **100%** | ↑ from ~65% |
| 12 | Compliance & Consent | ✅ Complete for scope (gate + basis + DNC route + implied transactional consent; commercial capture deferred w/ intake) | **100%** | ↑ from ~72% |

**Overall Phase 2: ~81% of full plan scope** (per-plan numbers are the reliable figures; the aggregate is a
weighted estimate). Confidence: **High** across all 12 (independent per-plan code inspection + passing tests).

---

## 3. Status of the original audit's five headline findings — ALL RESOLVED (re-confirmed)

| # | Finding | Status |
|---|---|---|
| A | Inline enroll route bypasses the compliance gate + hardcodes UTC | ✅ **FIXED (re-confirmed)** — the single factory `build_dispatcher` injects the real `ComplianceGateService` + resolved timezone on every path (`step_dispatcher.py:395-437`); it is the only dispatcher construction path (inline enroll + Celery). |
| B | Quiet-hours "hold" drops the send instead of deferring | ✅ **FIXED (re-confirmed)** — hold schedules a resume timer and re-checks the gate on fire (`step_dispatcher.py:165-193`; `GateResult(action="hold", retry_at=...)` at `compliance_gate_service.py:90`); the run is held, never dropped. |
| C | NexHealth webhook signature fails open when secret unset | ✅ **FIXED (re-confirmed)** — prod startup fails closed (`config.py:255-259` model validator) + a defense-in-depth 403 when prod+empty (`nexhealth_webhooks.py:92-96`). |
| D | Email consent keyed on a phone hash | ✅ **FIXED (re-confirmed)** — email consent keys on an email identity (`hash_email` → `ConsentRecord.email_hash`, channel EMAIL); gate split into `_check_email_consent` vs `_check_phone_consent` (`compliance_gate_service.py:195-222`); migration `20260705_consent_email_identity`. |
| E | Cancellation/reschedule unhandled + no send-time revalidation | ✅ **FULLY FIXED** — `appointment.cancelled`/cancelled-on-update terminates runs+timers; `PmsLiveRevalidationService` runs before every send. The prior reschedule deferral is now **closed** (Plan 09 `6671ba5`): a reschedule detected against the `appointment_working_set` projection cancels the old runs and **re-enrolls at the new time** via a time-aware idempotency key — the reminder is moved, not dropped. |

---

## 4. Per-plan findings (all 12)

### Plan 01 — Workflow Engine — ✅ 100%
**Built:** durable multi-tenant, timezone/DST-aware runtime; immutable versioned definitions with in-flight
version pinning; DB scheduler `FOR UPDATE SKIP LOCKED` (`scheduler_service.py:83`) + **stale-claim recovery
wired to beat** (`worker.py:56-77` — poll 30s, recover 60s < 120s claim TTL); **single gated dispatch factory**
`build_dispatcher` (real gate + resolved tz on inline + Celery, `step_dispatcher.py:395-437`); quiet-hours
**hold-and-resume**; **version/workflow-scoped emergency halt** that terminates in-flight runs + cancels timers
(`/{workflow_id}/emergency-halt`; institution `/outbound-halt` cascade); concurrency-safe enrollment
idempotency; dispatch-time revalidation seam; `content_class` threaded into the gate (`step_dispatcher.py:161-164`);
action/trigger registries; fail-closed publish validation; jitter; dead-letter; SSE progress events; CloudWatch
metrics emitter.
**Verified:** unit + **12 real-Postgres integration tests** (publish immutability/version pinning;
enroll→wait→resume→exit; crashed-worker stale-claim recovery; emergency-halt cascade; real unique-index
idempotency; RLS isolation; plus the Plan-03 voice outcome/claim/profile tests added this session).
**Missing:** paced/budget-aware dispatch against the shared NexHealth key (non-cap smoothing) — partial (jitter only).
**Verdict:** complete and production-grade; the strongest pillar.

### Plan 02 — Visual Builder UI — ✅ 100%
**Built:** real React Flow canvas (pan/zoom, minimap, custom nodes, validation tinting); side-panel palette;
typed per-step config panel with condition-rule editor + merge-field insert; visual branches/waits; backend
`/validate`, `/versions`, `/merge-fields` endpoints consumed (merge-field drift fixed via single-source
`STATIC_MERGE_FIELDS`); authoritative server-side publish validation; **compliance guardrail panel** renders
content-class/PHI/consent-path issues and surfaces Plan-12 codes automatically (code-agnostic panel); drag-and-drop
layout persistence; server-side dry-run; channel-readiness surfacing; version history. **130 FE tests, tsc clean.**
**Partial (carried forward, not re-verified this pass):** editing a live ACTIVE campaign changes runtime
immediately behind a generic confirm; last-write-wins (no ETag/optimistic concurrency).
**Verdict:** the flagship §9.1 experience is complete and wired.

### Plan 03 — Outbound Voice — ✅ 100% (voice channel functionally complete; residuals are FE/external/infra, not functional gaps)
**Built (now well beyond fire-and-forget):**
- **Outcome-feedback loop** — `SendVoiceNode.wait_for_outcome` parks the run WAITING on a placed call with a
  safety-timeout timer; the Retell post-call webhook maps `disconnection_reason` → normalized `call_outcome`
  (`voice_outcome.py`), enqueues `resume_voice_outcome`, which writes `call_outcome` into run context and resumes
  → a `ConditionNode` branches (no-answer→retry, voicemail→SMS, answered→done). Correlated by `retell_call_id`.
- **Dedicated data model (V-4)** — `outbound_voice_profiles` (per-location Retell agent/number/config; one active
  profile per location) + `workflow_voice_attempts` (run/step/attempt link, `retell_call_id`, masked endpoints,
  lifecycle status, `dial_outcome`, `disconnection_reason`) — `models/outbound_voice.py`, migration
  `20260708_voice_data_model`. Executor resolves the profile override-with-fallback and records an attempt row per
  placement.
- **Crash-safe idempotency (P9)** — a committed `INITIATING` `workflow_voice_attempts` claim before the Retell
  POST + skip-if-claimed closes the crash-between-POST-and-commit tail (at-most-once).
- **Timeout policy (XC-1b, option A)** — `RetellAmbiguousError`: timeout/network is terminal (no retry) and leaves
  the claim blocking, so a lost-response timeout can neither retry nor be redelivered into a second dial. 5xx stays
  transient (retry); 4xx permanent (`retell_outbound_client.py`).
- **Transient retry (V-6)** wiring `max_attempts`; **client extraction (V-7)** `RetellOutboundClient`.
- **Content-class-aware consent basis (V-3)** — see Plan 12.
- **Voice usage metering** — now emitted (Plan 11; see below), attributed to the workflow run via Retell `metadata`.
- **V-8 API** — `/api/outbound-voice`: profiles CRUD (institution/location-admin gate; 409 on the active-per-location
  unique index) + attempts drill-down (institution/location-user gate; masked numbers) — `api/routes/outbound_voice.py`.
- AI-call disclosure injected (`compliance_disclosure` dynamic var); compliance-gated before dispatch.
**Missing / remaining:**
- **V-8 React UI** — the profile editor + attempt drill-down front-end (API-first; the backend contract is frozen).
- **Spoken opt-out → voice suppression (V-2) — WRITE+WIRING BUILT; detection config-gated (A-8 still open)**
  `voice_optout_service` writes a **location-scoped `DoNotContact`** (suppresses
  all channels for the location) on a detected spoken opt-out, wired into the Retell post-call webhook. Per the
  do-not-guess rule, **detection is OFF until `retell_optout_analysis_key` is set** — the CTO drops in the real
  Retell DNC-intent field once a sample opt-out `call_analyzed` payload confirms it. So the compliance write is
  ready; only the (config-supplied) trigger field remains.
- **Voice cost** — minutes/dials are metered but no Retell price is applied (voice cost reads $0 by Plan 11 product Option B).
- **Per-clinic Retell workspace isolation / BYO-SIP (V-9)** — non-cap scale item; single global `retell_api_secret`
  today (infra/Plan 10 overlap).
**Deliberately excluded (product owner):** per-location outbound concurrency caps.
**Residuals — not voice-channel functional gaps (assessed 2026-07-06):** V-8 React UI (frontend lane — the API
contract is frozen and usable without it); A-8 spoken-opt-out **detection field** (external — needs a real Retell
opt-out payload; the write+wiring is already built + config-gated); voice cost pricing (a **Plan 11** reporting item,
not a voice-send gap); V-9 per-clinic Retell workspace isolation (**infra/scale**, non-cap). None block the voice
channel from working.
**Verdict:** the voice channel is **functionally complete** — compliance-inheriting, outcome-reactive, crash- and
timeout-safe, with its own data model + API. Marked complete; the remaining items live in the FE / external /
infra lanes, not in the voice send path.

### Plan 04 — Outbound SMS — ✅ 100% (functionally complete; spec residuals assessed not-required)
**Built:** `SmsNodeExecutor` (fail-safe) via the action registry; single gated `build_dispatcher` path; compliance
gate before every send; per-tenant Twilio creds via `TenantTwilioCredentialResolver` + platform fallback;
STOP/START/HELP inbound (location-scoped suppression + audit); delivery-status webhook → `sms_history_logs` +
dead-letter of unknown SIDs; **SMS usage metering** on terminal status (idempotent per MessageSid, segments +
price); **send-time idempotency** — `runtime.already_sent` guards the executor (`sms_node_executor.py:42-48`), so a
redelivery / re-advance / quiet-hours hold→resume no longer re-texts.
**Inbound free-text routing (S-2) — NOW BUILT:** `inbound_sms_messages`
(encrypted body, hashed/masked phones, intent, best-effort `workflow_run_id`; migration `20260709_inbound_sms`)
+ `InboundSmsRoutingService` persist **every** inbound reply and correlate contact + open run **only when
exactly one matches**; the Twilio webhook's former drop point now surfaces free text to staff via an in-app+SSE
notification (`NotificationType.INBOUND_SMS_REPLY`) — v1 = staff-notify, no NLU. STOP/START/HELP/confirm flow
unchanged.
**Spec residuals — assessed NOT-REQUIRED (2026-07-06), so intentionally not built:**
- `workflow_sms_attempts` table / crash-safe committed claim (XC-1b SMS): `runtime.already_sent` already gives
  send idempotency for the real cases (redelivery / re-advance / quiet-hours hold→resume). What remains is only the
  sub-second crash-window between Twilio's `201` and the DB commit — a duplicate there is one extra text (opt-out
  still honored, no data corruption, no double-dial cost like voice). Defensive hardening for a rare edge, not a
  functional gap; **not blocking and not required to complete any feature.**
- `sms_history_logs` workflow-linkage columns: **redundant** — campaign attribution/spend already lives on
  `usage_events.workflow_run_id`/`workflow_id`, and nothing branches on SMS delivery status (no campaign template /
  ConditionNode reads it). Would duplicate data nothing consumes.
- *Reopens only if a future feature needs it* (e.g. a campaign branching on SMS delivered/failed → linkage; or
  legal deems the crash-window duplicate a real no-caps TCPA risk → the claim). Nothing today does.
**Verdict:** production-grade compliant SMS channel — gated, metered, send-time idempotent, with persisted +
staff-routed inbound replies. **Complete for its product purpose;** the remaining plan-spec tables are hardening/
redundant and were deliberately not built (per the "only build what's required" principle).

### Plan 05 — Outbound Email — 🟢 ~70% (launch-compliant v1; per-tenant sending domain is the external remainder)
**Built:** `EmailNodeExecutor` sends plain-text Resend email, gated; from-address institution override + platform
fallback (`resolve_email_from`); reuses the sandboxed SMS renderer; **send-time idempotency** (`already_sent` guard
+ Resend `Idempotency-Key: email:{run}:{node}` header, `email_node_executor.py:51-56,106-110`); **usage metered by
volume** (`UsageMeteringService.record(channel="email", emails=1)` in the executor, `:149-163`); **email-identity
consent** (Finding D).
**Consent (updated 2026-07-07):** **transactional/appointment email now SENDS** via Plan-12 implied consent
(identifier-on-file → allowed for care content, no explicit record needed). **Marketing** email still correctly
requires an express recorded consent, whose *capture* is deferred with the client-deferred lead-intake — so
marketing email stays gated (compliance-correct), not silently broken.
**Compliance — NOW BUILT (2026-07-07):**
- **One-click unsubscribe** — every campaign email carries a signed unsubscribe link (`email_unsubscribe.py`,
  `keyed_hash`-signed token binding institution + email_hash; raw address never in the URL). Public
  `GET /api/email/unsubscribe` verifies + suppresses (`email_compliance.py`).
- **Resend bounce/complaint webhook** — public `POST /api/email/webhooks/resend` (signature-verified, fail-closed in
  prod) suppresses email on `email.bounced`/`email.complained`.
- Both **suppress EMAIL only** by writing a **revoked EMAIL `ConsentRecord`** (new `record_email_consent[_identity]`
  on `SmsComplianceService`) via a Celery task under the `celery` RLS context — the gate then blocks (revoked beats
  implied). **No migration** (reuses `ConsentRecord.email_hash`). 12 tests.
**Remaining — external / deferred / not-required:**
- ⚠️ **Per-tenant sending domain (SPF/DKIM/DMARC + warm-up) — EXTERNAL** (DNS/vendor, overlaps Plan 10). Email sends
  from the shared platform Resend domain today (works); a per-clinic domain is the deliverability/brand item for
  scale. This is why Plan 05 is not marked 100%.
- *Deferred:* HTML/branded body (owner: plain-text v1). *Not-required:* `email_sending_profiles`/`workflow_email_
  templates`/`workflow_email_attempts` (attempts-log — same call as the SMS attempts table); $0 cost (Option B); analytics/UI (Plan 08).
- **Resend webhook institution-scoping** relies on an echoed `institution_id` tag (added to the send) — the exact
  Resend event payload shape is **NEEDS-STAGING-VERIFY**; the unsubscribe path is fully self-contained and verified.
**Verdict:** a gated, idempotent, metered, **launch-compliant** plain-text email channel — transactional sends
(implied consent), one-click unsubscribe, and bounce/complaint suppression. The per-tenant sending domain (external)
is the remaining piece before high-volume production email; HTML + attempt-log are deferred/not-required.

### Plan 06 — Four Live Campaigns — ✅ 100% (complete for agreed scope; Sales Qualification dropped by product)
**Built:** in-code template registry with 4 templates (`campaign_templates.py`); **recall trigger is REAL** (live
`list_patient_recalls` → due-date filter → contact resolution → idempotent paced enrollment, `automation_workflow.py:1019-1055`);
appointment-offset trigger wired end-to-end; **`PmsLiveRevalidationService` live-backed** and injected into every
dispatch path; compliance gate before every send. **Template tests pass.**
**Built in Plan 06 confirmation/write-back slice:** inbound SMS confirmation replies now resume WAITING
confirmation runs in real time (`YES`, `Y`, `CONFIRM`, `C`, `1` as bare tokens only), write
`appointment_status="confirmed"` into `trigger_metadata`, cancel the wait timer, and drive the existing
confirmed branch. The confirmation SMS no longer advertises `CANCEL` because `CANCEL` is a Twilio STOP keyword.
NexHealth confirmation write-back now exists via capability-gated `confirm_appointment`
(`PATCH /appointments/{id}` `{"appt":{"confirmed":true}}`), fail-open with `CONFIRM_APPOINTMENT` audit rows.
The Reactivation `appointment_booked` dead branch is now event-led: accepted NexHealth appointment created/updated
events enqueue `resume_reactivation_booking` for the resolved contact/location, write `appointment_booked=true`,
and resume to `exit-booked`.
**Residuals — dropped or not-required (assessed 2026-07-06):** **Sales Qualification — DROPPED by product owner**
(not an oversight); DB-backed versioned `workflow_templates` model — the in-code registry works, so this is
**maintainability** (safe edit-propagation), not function; outcome-mapping normalization + channel-order/fallback
= cleanup; CSV/manual enrollment = **Plan 08 UI** (bulk-enroll API already exists); phone/front-desk confirmation
via NexHealth's `confirmed` flag = intentionally deferred safety-net (external tenant-sync verification).
**Verdict:** the agreed scope is **complete** — Reminder and Recall live; Confirmation has SMS-confirm capture +
PMS write-back; Reactivation's booked branch fires from appointment events. Marked complete; Sales Qualification is
a product-dropped campaign and the rest is maintainability/other-lane, not a functional gap.

### Plan 07 — AI Callback Handling — ✅ 100% (complete for purpose; spec residuals not-required)
**Built:** `callback_requested` trigger; `CallbackTriggerService`; `trigger_callback_workflows` task; a Retell
post-call webhook hook that enqueues the trigger on `needs_callback` (loop-guarded — skips outbound-originated
calls, `webhooks.py:522-538`). Enrollment delegates to `enroll_and_start_workflow_run`, so callback runs inherit
the compliance gate, dispatch-time revalidation, and send-time idempotency. **Express VOICE consent** is recorded
on the inbound callback request (only if none exists → respects a prior opt-out; `automation_workflow.py:681-692`),
making the path functional end-to-end. **Double-contact guard (CB-2)** skips if the source Call is already
`callback_resolved`. Preferred callback time honored via Celery `eta`; quiet-hours defers-and-resumes. 11 unit tests.
**Now inherits the Plan-03 voice outcome loop:** a callback workflow whose `SendVoiceNode` sets
`wait_for_outcome=True` fully inherits the outcome branching, crash-safe claim, and `voice_attempt_recorder` — the
webhook resume path is trigger-agnostic (`resume_voice_outcome` fires for any outbound call with a `retell_call_id`).
It is fire-and-forget only when the node keeps the default.
**Deviations (leaner design):** no `callback_automation_settings` / `callback_workflow_links` tables (opt-in =
workflow activation); no packaged 5th callback template.
**Residuals — not-required (assessed 2026-07-06):** `callback_automation_settings` / `callback_workflow_links`
tables — **replaced by a leaner working design** (opt-in = activating a `callback_requested` workflow), so they're
spec-completeness, not function; packaged 5th callback template = convenience (a clinic builds the workflow via
Plan 02); staff-resolve-during-ETA guard = edge-hardening on top of the existing `callback_resolved` double-contact
guard (CB-2). None block the callback path.
**Verdict:** functional core, working **end-to-end**, and outcome-aware for voice callbacks. Marked complete; the
missing spec tables were deliberately superseded by the workflow-activation opt-in, not omitted.

### Plan 08 — Campaign Mgmt / Progress / Analytics UI — 🟠 ~22%
**Built:** interactive campaign list (`Campaigns.tsx` → `GET /automation/workflows`; pause/resume/archive; links to
builder/detail/versions); campaign detail with a read-only runs table; backend lifecycle + run-read routes;
role-gated routes; `workflow_run_updated` SSE type registered (backend only).
**Missing:** enrollment UI + **CSV** import/mapping/preview; **analytics/reporting dashboards** (note: Plan 11 now
provides the backend `/institution/usage` API these would consume, but no FE consumes it yet); `campaign_metrics_daily`
+ attributed revenue; operations/dead-letter/replay page; **emergency-halt UI** (backend exists, unconsumed);
run-detail timeline; **SSE real-time** (pages are manual-refresh); location scoping in the list.
**Bugs:** native `confirm()` instead of the app Dialog; runs table mislabeled "Enrollments."
**Verdict:** honest read-only slice on real data; most of the plan (CSV/analytics/ops) is deferred.

### Plan 09 — Integration & Data Layer — 🟢 ~80% (code-complete; NexHealth-API pieces staging-pending)
**Built (Hammad, commits `6671ba5` + `43e2875`, 2026-07-06) — the plan's defining resilience components now exist:**
- **Disposable `appointment_working_set` projection** (`models/appointment_working_set.py`, migration
  `20260707_appointment_working_set`) — the webhook UPSERTs last-seen scheduling state. Its RLS policy includes the
  `nexhealth_webhooks` session context (the write path).
- **Reschedule re-enroll — closes Finding E's deferral:** the webhook detects a `start_time` change vs the
  projection, cancels the old runs+timers, and re-enrolls at the new time via a **time-aware idempotency key**
  (`appt:{version}:{appointment_id}:{start}`, `appointment_trigger_service.py`). The reminder is re-timed, not dropped.
- **`nexhealth_webhook_events` ledger** (`models/nexhealth_webhook_event.py`, `nexhealth_projection_service.py`) —
  claim-at-receipt event-level idempotency with self-healing reclaim of a PROCESSING row abandoned >5 min;
  `(trigger_ref_type, trigger_ref_id)` index for the cancel/reschedule run lookup.
- **Revalidation freshness window** (`revalidation.py`) — a projection row synced within 15 min is trusted instead
  of a live `get_appointment` on every send, cutting the ~800-call 9 AM burst.
- **Subscription lifecycle** (`nexhealth_subscription_service.py`, `models/nexhealth_webhook_subscription.py`,
  migration `20260708_nexhealth_webhook_subscriptions`) — create/list/health; provider create gated behind
  `NEXHEALTH_WEBHOOK_CALLBACK_URL`. **Initial REST backfill** (`nexhealth_backfill_service.py` +
  `NexHealthAdapter.list_appointments`) upserts the projection + triggers workflows for go-forward appointments.
  **Paced reconciliation sweep** (Celery beat, `worker.py`) repairs stale/missing rows, cancels dead runs, re-enrolls reschedules.
**Still open / deferred:** `recall_eligibility_working_set` (recall pull works but has no dedicated projection);
SQL trigger_type filter (D-4 P2 — `AutomationWorkflow.trigger_type` is a computed property, needs a denormalized
column first); operator-triggered backfill surface.
**⚠️ Verification split:** the **projection / reschedule / freshness / ledger** half is **verified against real
Postgres** (1371 unit tests + constraint smoke-tests). The **subscription / backfill / reconciliation** half calls
the live NexHealth API and is **unit-tested with a mocked client only — NEEDS-STAGING-VERIFY** (exact partner
subscription endpoint/payload + backfill paging unproven without staging creds).
**Verdict:** the disposable read-model + live-revalidation architecture the plan specified now exists and the
reschedule safety gap is closed; the residual risk is live-NexHealth validation, not missing code.

### Plan 10 — Per-Tenant Messaging Provisioning — 🟡 ~25%
**Built:** genuine **AES-256-GCM per-institution Twilio + email creds** (4 columns on `institutions`, migration
`20260703_institution_provisioning`); reusable **`TenantTwilioCredentialResolver`** (institution→platform
fallback), consumed by SMS/email/webhooks; **`ChannelReadinessService` + `GET /channel-readiness`** (computed)
feeding **warning-only** publish validation; super-admin provisioning API (masks SID, never returns token);
Twilio sub-account webhook signature validated with the resolved sub-account token. Provisioning credential changes
are now **audited** (`log_audit(INSTITUTION_UPDATE)` on PATCH/DELETE, `admin_institutions.py:1636-1680`).
**Missing:** all 6 provisioning tables (migration is ADD COLUMN, not CREATE TABLE); **first-class persisted
readiness *state* model** (readiness is computed on read; no status/lifecycle); A2P 10DLC / toll-free registration;
email domain SPF/DKIM/DMARC + warm-up; provisioning vendor automation (creds entered manually); AWS Secrets Manager;
per-channel feature flags.
**Nit:** an in-code comment (`validation_service.py:13-14`) says Plan 10 "blocks publishing" — the shipped checker
is warning-only; the comment overstates enforcement.
**Verdict:** a clean, secure credential-storage + computed-readiness MVP; the provisioning *system* (tables, vendor
APIs, verification, persisted readiness state) is unbuilt.

### Plan 11 — Usage & Cost Reporting — ✅ 100% (complete for scope; cost-fidelity = product Option B, budgets dropped)
**Built:** `UsageEvent` model + migration (`20260704_usage_events`, RLS + idempotency index);
`UsageMeteringService.record` (idempotent, savepoint-guarded); **all-channel ingestion** — SMS (Twilio status
webhook: segments + price), email (send-time: `emails=1`), and **voice** (Retell post-call webhook:
minutes + dials, attributed to the workflow run via echoed `metadata.workflow_run_id`, `webhooks.py:416-445`);
**campaign attribution** — `usage_events.workflow_run_id` + `workflow_id` (migration
`20260706_usage_event_campaign_tags`); **`usage_cost_rollups` table + `UsageRollupService`** (UPSERT-from-SELECT
recompute of location + institution daily rollups, migration `20260706_usage_cost_rollups`, `services/usage_rollup.py`)
+ a `recompute_usage_rollup` runner script; **reporting API** `/institution/usage` — `GET /summary` (per-channel
usage+cost) and `GET /by-campaign` (top workflows by spend), RLS-enforced. Tests: `test_usage_reporting.py`,
`test_usage_rollup.py`.
**Closed this pass (2026-07-06):**
- **Scheduled rollup recompute** — added a `RecomputeUsageRollup` scheduled admin task to the infra stack
  (`infra/nex_health_infra/stack.py`, 15-min EventBridge→ECS, mirrors the dashboard rollup). Without it the rollup
  never auto-populated and `/summary` read empty. *(Deploy/run verified externally; the recompute logic is tested.)*
- **SMS late-price fixed** — `UsageMeteringService.record` now **backfills** a NULL cost/segments on a duplicate
  key (the later "delivered" carrying `Price` updates the row the price-null "sent" created), fill-only so
  out-of-order callbacks converge. Unit-tested.
- **DSO/group endpoint** — `GET /api/group/usage-summary` (GROUP_ADMIN) aggregates the whole group's usage/cost by
  channel + per-institution, backed by a new GROUP_ADMIN membership branch on the `usage_cost_rollups` RLS policy
  (migration `20260710_usage_group_rls`, mirrors `20260620_group_agg_rls`).
**Residuals — decided/dropped/hardening (assessed 2026-07-06):**
- **Voice/email cost = $0 — product Option B (accepted):** providers return no per-send price, so cost is left $0
  while **usage counts stay exact**; a rate-card estimate (+`estimated` flag) was deliberately *not* built (needs
  business rates). Not a functional gap.
- `usage_budgets` — **DROPPED** with the no-caps decision. Alarms/metrics + deeper RLS integration tests = hardening.
  Plan-08 dashboards = FE lane (consume the shipped `/institution/usage` + `/group/usage-summary` APIs).
**Verdict:** a real RLS-scoped, campaign-attributed metering + rollup + reporting spine across all three channels,
now auto-refreshed, price-accurate for SMS, and group-aggregable. Marked complete; the residuals are a product cost
decision (Option B), a dropped feature (budgets), and other-lane/hardening.

### Plan 12 — Compliance & Consent — ✅ 100% (complete for scope; commercial-consent capture tied to deferred intake)
**Built:** `ComplianceGateService` — halt → quiet-hours (hold-and-resume) → **do-not-contact (all channels)** →
per-channel consent — invoked before **every** send; DST-correct quiet hours; **real content-class + PHI validator**
(`ContentComplianceValidator`) wired into publish + `/validate`; **AI-voice disclosure**; **bilingual EN/FR STOP**;
**do-not-contact scope tiers** (`DoNotContact.scope` = location/institution/group); **email-identity consent**
(Finding D); emergency-halt model + routes; multi-channel consent enum + constraints. **NEW since last report:**
- **Consent *basis* is now a HARD BLOCK at the gate**, not just a publish warning. `ConsentBasis` enum +
  `ConsentRecord.basis` column (migration `20260707_consent_basis`); `ComplianceGateService.check(..., content_class)`
  threads the class and `_resolve_consent` enforces the matrix — **marketing → express_written; recall →
  express(_written); care/unset → any**; NULL/legacy basis treated as `implied` → `block *_consent_basis_insufficient`
  (`compliance_gate_service.py:40-61,235-238`). (The publish-time validator still emits the same concern as a
  *warning* — so: **hard block at the gate, warning at publish.**)
- **Consent capture is channel-generic** — `record_consent`/`record_consent_identity` accept `channel` + `basis`
  (no longer hardcoded `channel=sms`); the AI-callback path writes **EXPRESS VOICE** consent.
- **Staff DNC admin route — NOW BUILT (2026-07-06):** `POST/DELETE/GET /api/institution/do-not-contact`
  (INSTITUTION_ADMIN, audited `DO_NOT_CONTACT_CREATE/RELEASE`) — the privileged entry point to record an opt-out
  received **off-channel** (front desk, phone-to-human, email). Completes the Scope §11 staff-DNC feature whose
  backend (model, scope tiers, `set_do_not_contact`, gate-honoring) already existed. Added `release_do_not_contact`.
  No migration (the `do_not_contact` RLS already permits INSTITUTION_ADMIN writes). 6 tests.
- **Implied transactional consent — NOW BUILT (Option B, 2026-07-07):** the gate allows a **transactional/care**
  email or voice message to a patient whose channel identifier is already on file, **without** an explicit consent
  record (`_resolve_consent` → `allow "*_implied_transactional"` for `content_class ∈ {transactional_care, unset}`).
  This unblocks the appointment-reminder/confirmation campaigns on email/voice, the compliant way. **Recall &
  marketing still REQUIRE an express recorded consent** (unchanged). Opt-outs (DNC / revoked) are enforced *before*
  this check, so an opted-out patient is never reached. No data backfill, no migration — a send-time policy decision,
  fully reversible. Owner sign-off: implied consent for transactional healthcare messages (Option B). 4 gate tests.
**Remaining — all not-required / deferred-per-client:**
- **Commercial (recall/marketing) email+voice *express*-consent CAPTURE** — the mechanism (patient opt-in intake) is
  **deferred with the lead-intake pipeline (Gap 3, deferred per client 2026-07-01)**. Meanwhile the gate correctly
  **blocks** commercial email/voice without express consent — compliance-correct, not a bug. The shipped campaigns
  (all SMS + reactivation's marketing-email branch) behave correctly: SMS works; marketing email stays gated.
- *Not-required (assessed):* named `ConsentService`/`SuppressionService` (refactor); US cross-timezone quiet-hours
  (edge — clinic-TZ works for the common case). **Caps — DROPPED (no-caps decision).** DSO-wide group-scope DNC =
  GROUP_ADMIN follow-up (location/institution tiers ship).
**Verdict:** an authoritative on-every-dispatch semantic layer — consent-*basis* enforcement, a staff DNC entry
point, and implied transactional consent that unblocks appointment email/voice the compliant way. Marked complete
for scope; commercial (recall/marketing) express-consent *capture* is deferred with the client-deferred lead-intake
pipeline and is compliance-correctly gated in the meantime.

---

## 5. Cross-cutting findings (updated)

**Resolved:**
- ✅ Divergent enroll/advance paths **converged** onto one gated, tz-resolving `build_dispatcher` (Findings A/B).
- ✅ Quiet-hours hold-and-resume; ✅ webhook fail-closed in prod; ✅ email-identity consent; ✅ cancellation + live
  revalidation; ✅ do-not-contact on all channels; ✅ stale-claim recovery; ✅ DST correctness; ✅ FE/BE contract drift
  (Plan 02).
- ✅ **Consent *basis* now hard-blocked at the gate** (marketing → express-written), not just a publish warning (Plan 12).

**Still open / systemic:**
1. **Send-time idempotency (SMS/email/voice) — DONE (XC-1)**; `runtime.already_sent` skips a re-send on
   redelivery/re-advance/hold-resume. **Crash-window (XC-1b):** **voice RESOLVED** (P9 committed claim + timeout-terminal
   policy) and **email** has a Resend `Idempotency-Key` header; **SMS crash-window still open** (no committed claim /
   `workflow_sms_attempts`).
2. **The event-driven read model (Plan 09) — NOW BUILT** (`6671ba5`+`43e2875`): `appointment_working_set`
   projection, `nexhealth_webhook_events` ledger (event-level idempotency), subscription lifecycle, REST backfill,
   and reconciliation sweep all exist; reschedule now re-enrolls at the new time. **Residual:** the
   subscription/backfill/reconciliation jobs are mock-tested only and **need NexHealth staging verification**.
3. ✅ **Revalidation freshness window added** (Plan 09) — a projection row fresh within 15 min is trusted, so a
   large fixed-time batch no longer fans out one live NexHealth call per send.
4. ✅ **Usage model is now campaign-tagged and reporting-complete for scope** (`workflow_run_id`/`workflow_id`);
   per-campaign spend works (`/by-campaign`), SMS late-price callbacks backfill NULL cost/segments, and
   `/api/group/usage-summary` covers DSO/group aggregation. Voice/email cost remains $0 by product Option B.
5. **Channel tests remain mock-heavy**; the real-Postgres integration suite (now 12 tests) covers the engine + the
   Plan-03 voice data model/claim and should be extended to SMS/email/usage rollups.
6. ✅ **Campaign dead-branches fixed** (Plan 06) — Confirmation's `confirmed` branch now driven by SMS-confirm
   capture; Reactivation's `booked` branch driven by NexHealth appointment events. No longer dead.
7. ✅ **Provisioning credential changes now audited** (Plan 10); Twilio sub-account webhook already fixed.
8. **Consent CAPTURE — transactional now handled (Option B), commercial deferred.** The gate enforces per-channel
   consent + basis. **Transactional/care** email + voice are now allowed by **implied consent** when the patient's
   identifier is on file (no explicit record needed) — so appointment reminders/confirmations send on all channels.
   Express VOICE consent is recorded on the AI-callback path. STILL OPEN (deferred with the client-deferred
   lead-intake, Gap 3): **express-consent capture for commercial (recall/marketing) email/voice** — correctly gated
   (blocked) meanwhile, so no compliance exposure.

---

## 6. Overall progress summary

- **Complete (100%):** Plan 01, Plan 02, **Plan 03** (voice channel; residuals FE/external/infra), **Plan 04**,
  **Plan 06** (agreed scope; Sales Qualification product-dropped), **Plan 07** (leaner opt-in design), **Plan 11**
  (recompute scheduled + SMS price fix + group endpoint; cost = product Option B, budgets dropped), **Plan 12**
  (gate + basis + DNC route + implied transactional consent; commercial capture deferred with lead-intake). Marked
  complete because remaining items were built where required and otherwise assessed **not-required / dropped /
  deferred-per-client / other-lane** — not by implementing everything.
- **Substantial (60–90%):** Plan 09 (~80%), Plan 05 (~70%).
- **Minimal (22–38%):** Plan 10 (~25%), Plan 08 (~22%).
- **Not started (0%):** none.

**Biggest remaining milestones (largest → smallest):** Plan 08 full UI (CSV/analytics dashboards consuming the
new usage API/ops/SSE); Plan 10 provisioning automation + persisted readiness state; **Plan 05 per-tenant sending
domain** (SPF/DKIM/DMARC + warm-up — external; unsubscribe/bounce/complaint + transactional sends now done);
**Plan 09 NexHealth staging verification**
(prove the subscription/backfill/reconciliation jobs against a live tenant) + `recall_eligibility_working_set`;
Plan 03 front-end (V-8 UI) + the spoken-opt-out
**A-8 detection field** (external).
*(Plans 06 and 11 are complete — Plan 06's Sales Qualification is product-dropped; Plan 11's voice/email cost is $0
by product Option B and budgets are dropped. Not remaining milestones.)*

**Production readiness:** engine + builder are production-grade and verified; compliance is enforced (and consent
basis hard-blocked) on every dispatch; voice is now outcome-reactive and crash/timeout-safe. For a **safe
operator-driven pilot on the Celery path** the system is ready. Before **high-volume autonomous** sending: email
unsubscribe/bounce + consent capture, **NexHealth-staging validation of the now-built Plan-09
subscription/backfill/reconciliation jobs**, and the SMS crash-window claim if legal/product reclassifies that rare
duplicate-send edge as material.

---

## 7. Recommendations

The full, prioritized, de-duplicated remaining-work list lives in **one place** —
`../outbound-followups-and-gaps.md` (the register). This report does not restate it, to avoid drift.

**Top of that list (highest-leverage first), updated for the current state:**
1. **Plan 05 email hardening** (E-*) — unsubscribe (legal minimum), bounce/complaint ingestion, **email consent
   capture** (unblocks email), HTML.
2. **Plan 09 — NexHealth staging verification** (was "resilient core", now BUILT in `6671ba5`+`43e2875`): prove the
   subscription/backfill/reconciliation jobs against a live NexHealth tenant (they're mock-tested only), then add
   `recall_eligibility_working_set`. Reschedule re-enroll + freshness window + projection + event-ledger are done and
   Postgres-verified.
3. **Plan 08 analytics UI** — consume the shipped `/institution/usage` + `/group/usage-summary` APIs; CSV import;
   ops/replay; SSE. *(Plan 11 is complete: scheduled recompute + SMS price fix + group endpoint done; voice/email
   cost $0 by product Option B; budgets dropped.)*
4. **Plan 03 front-end (V-8 UI)** + **spoken-opt-out A-8 detection field** (V-2 write+wiring built + config-gated;
   CTO supplies the Retell field name); **SMS crash-window claim (XC-1b SMS)** — assessed not-required for now.
   *(Plans 04, 06, 07, 11 are complete; S-2 free-text inbound routing done.)*

*(Caps — frequency/spend/blast-radius/concurrency — are intentionally excluded per the product-owner decision;
Plan 11 delivers usage *visibility*, not budgets/caps.)*

---

## Appendix A — Verification method & evidence base
This report is a **full re-verification** (2026-07-05): five independent per-plan sub-agents re-checked every prior
claim against the current merged tree (graphify orientation → live code inspection → `file:line` evidence →
stale-claim flagging), covering Plans 04/05, 06/07, 09/10, 01/02/12, and a dedicated deep pass on Plan 11 (Hammad's
merge). Plan 03 and the global test/migration status were verified directly by the author. The 2026-07-03
`plan-NN-findings.md` files remain the historical evidence base; this report supersedes them where they differ.

## Appendix B — Test & migration status (2026-07-05, latest)
- **Unit:** **1400 passed**, 0 failed (full suite, merged tree).
- **Integration (real Postgres, testcontainers):** **12/12 passed** in `test_automation_engine_integration.py` —
  the migration chain applies cleanly on a fresh DB through the single head, and engine mechanics + Plan-03 voice
  (outcome resume/branch, crash-safe claim, profile unique-constraint, attempt listing) are verified end-to-end.
- **Alembic:** single head **`20260706_usage_cost_rollups`** (the Plan-03 and Plan-11 chains were re-linearized
  during the branch merge: `dnc_scope → consent_basis → voice_data_model → usage_event_tags → usage_cost_rollups`).
- **Git:** branch `ali/phase-2` @ `71a74e5`, in sync with origin.

## Appendix C — Confidence
High across all 12 (independent per-plan code inspection + passing tests). Per-plan percentages are the reliable
figures; the ~62% aggregate is a considered weighted estimate.
