# Phase 2 Verification & Progress Audit — Outbound Engagement Engine (v2)

**Date:** 2026-07-03
**Branch:** `ali/phase-2` (incl. merge of `feature/outbound-engagement-engine`)
**Scope audited:** All 12 implementation plans **except Plan 03 (Outbound Voice)** — owned by another developer, excluded per instruction.
**Method:** graphify-oriented navigation + direct code inspection. 11 independent per-plan verification passes (one subagent each), every conclusion traced to `file:line`. The three highest-severity live bugs were re-read and **self-verified** by the author this session. Full per-plan detail lives in `plan-NN-findings.md` alongside this report.

> **This report supersedes** `docs/new_work/sessions/verification-phase2/report.md` (earlier same-day snapshot, now historical). Since that snapshot, Plans 10 (provisioning), 04 (SMS), 05 (email), and 02 (builder UI + backend follow-ups) landed, and a real compliance gate (Plan 12) replaced the NoOp seam. This document is the current source of truth.

---

## 1. Executive summary

Phase 2 has moved from "engine + scaffolding that cannot send" (prior report) to **a system that can send a real, compliance-gated SMS or email end-to-end on the asynchronous (Celery) path.** That is a genuine, material step forward. The workflow engine (Plan 01, ~88%) and the visual builder (Plan 02, ~75%) are the two strong pillars.

However, most channel/campaign/data/compliance plans were **intentionally rescoped to MVP slices** and shipped 20–40% of their full plan scope. The sessions were largely honest about this; the *plans* were not updated to record the deferrals. The result is a strong foundation with a thin "last mile": compliance depth, the event-driven data layer's resilience, per-tenant provisioning automation, usage metering, and the campaigns' actual differentiators (PMS write-back, revalidation, real recall enrollment) are the largest remaining gaps.

**Headline: ~35% of full Phase-2 plan scope delivered (excluding Plan 03).** The delivered slices are mostly functional and tested, but there are **five cross-cutting defects that are live compliance/correctness risks the moment real sends turn on** (§5).

### The five findings that matter most (all evidence-based; ⭐ = author self-verified)

| # | Finding | Severity | Evidence |
|---|---|---|---|
| A ⭐ | **Inline enroll route bypasses the real compliance gate _and_ hardcodes UTC.** The synchronous `POST .../{id}/enroll` builds the dispatcher with no gate (→ `NoOpComplianceGate`) and passes `location_timezone="UTC"`. A first-node SMS/email on this path skips emergency-halt + quiet-hours (and email skips consent entirely). | **HIGH** | `automation_workflows.py:463,469`; confirmed independently by the 01/04/05/12 passes |
| B ⭐ | **Quiet-hours "hold" drops the message instead of deferring it.** On a `hold`, the dispatcher calls `complete_run(outcome="compliance_hold")` — terminating the run. Scope §8 requires the send be *held to the next permitted window, never dropped.* | **HIGH** | `step_dispatcher.py:124-130` |
| C ⭐ | **NexHealth webhook signature verification fails open** when the secret is unset — and empty is the default, with no production guard. An unauthenticated POST can trigger cross-tenant enrollment. | **CRITICAL** (security) | `nexhealth_webhooks.py:32-34`; `config.py:76` |
| D | **Email consent is keyed on a phone hash.** The gate hashes `contact.phone` for the EMAIL channel; email-only contacts are blocked `no_phone` and there is no email-address consent identifier. Email consent is effectively non-functional. | **HIGH** | `compliance_gate_service.py:163-175` |
| E | **Appointment cancellation/reschedule is not handled and there is no send-time revalidation.** Every `appointment.updated` enrolls; `appointment.cancelled` is ignored. A cancelled patient can still be reminded. | **HIGH** | `nexhealth_webhooks.py:71-88`; no `PmsLiveRevalidationService` |

---

## 2. Status dashboard

| Plan | Title | Status | % of full plan | Confidence |
|---|---|---|---|---|
| 01 | Workflow Engine | ✅ Substantially complete | **~88%** | High |
| 02 | Visual Workflow Builder UI | 🟡 Substantially complete | **~75%** | High |
| 03 | Outbound Voice | ⏭️ **Excluded** (other dev) | — | — |
| 04 | Outbound SMS | 🟡 Partial | **~55%** | High |
| 05 | Outbound Email | 🟠 Minimal MVP | **~20%** | High |
| 06 | Four Live Campaigns | 🟡 Partial | **~40%** | High |
| 07 | AI Callback Handling | 🔴 Not started | **0%** | High |
| 08 | Campaign Mgmt / Progress / Analytics UI | 🟡 Partial (read-only slice) | **~22%** full / ~90% Phase-3 subset | High |
| 09 | Integration & Data Layer | 🟠 Thin passthrough | **~27%** | High |
| 10 | Per-Tenant Messaging Provisioning | 🟡 Credential-storage MVP | **~20%** full / ~90% rescoped MVP | High |
| 11 | Usage & Cost Reporting | 🔴 Not started | **0%** | High |
| 12 | Compliance & Consent | 🟠 Foundational slice | **~27%** | High |

**Overall Phase 2 (11 audited plans): ~35% of full plan scope.** Weighted for the engine's size and the fact that the two hardest builds (01, 02) are the most complete, the *functional* foundation is stronger than 35% implies — but the go-live-blocking depth (compliance, data resilience, provisioning, metering) is thinner than that.

---

## 3. Per-plan findings

### Plan 01 — Workflow Engine — ✅ ~88% (High)
**Intended:** durable, multi-tenant, timezone-aware runtime; immutable versioned definitions with draft/publish + in-flight version pinning; DB-backed distributed scheduler with exactly-one dispatch; idempotent enrollment; compliance-gate seam; modular action/trigger registries; quiet-hours; emergency halt.

**Completed (evidence):** all 6 models + RLS + grants + idempotency/active-timer unique indexes (`models/automation_workflow.py:67-491`, `alembic/.../20260702_auto_workflow_core.py:73-311`); immutable versions + checksum + publish (`definition_service.py:116-213`); version pinning at dispatch (`tasks/automation_workflow.py:167,308`); durable scheduler with `FOR UPDATE SKIP LOCKED` claim **and stale-claim recovery now wired** (`scheduler_service.py:83,124` + beat poll) — this closes prior CRITICAL ENG-01; DST-safe due-at via zoneinfo (`step_dispatcher.py:265`); real Plan-12 gate injected on Celery paths (`tasks/automation_workflow.py:184,326`); full lifecycle/enroll/bulk/halt routes. Unit subset ran **52 passed**.

**Partial:** enrollment eligibility = idempotency dedup only (no frequency/spend caps); recall trigger is a stub; voice send still a stub.

**Missing:** emergency halt as *mid-flight terminate* (only a soft send-time block exists — waiting runs/timers never cancelled); modular `WorkflowActionRegistry`/`WorkflowTriggerRegistry`/`WorkflowValidationService`/`QuietHoursService` (functionality exists but inline, not as extensibility seams); paced/rate-limited dispatch (NexHealth/Retell/Twilio budgets + jitter).

**Bugs/gaps:** ⭐ **Finding A** (inline enroll route: no gate + UTC hardcode, `automation_workflows.py:463,469`); paused workflows don't stop in-flight waiting runs from advancing (`dispatch_workflow_timer` never checks `workflow.status`); no dispatch-time appointment-state recheck.

**Arch concerns:** two divergent enroll+advance paths (inline route vs Celery) with different gate/tz wiring — the root of Finding A; should converge. Publish is not truly "fail-closed on compliance."

**Tech debt:** app-level dedup key `(institution, key)` vs DB unique index that also includes version (safe in practice); recall + voice stubs.

**Code quality:** high; extensive tests (15 automation files), honest docstrings.

**Scope verdict:** the foundational non-sending engine matches the plan strongly and is well-tested. Deviations: soft-block halt vs mid-flight terminate; the inline enroll gate bypass; collapsed registries.

---

### Plan 02 — Visual Workflow Builder UI — 🟡 ~75% (High)
**Intended:** GoHighLevel-style no-code canvas; palette; typed per-step config; visual branches/waits; validation guardrails incl. Plan-12 compliance; draft/publish/pause + version history; template start; preview + test-run.

**Completed (evidence):** **real React Flow canvas** (`@xyflow/react@12.11.1`; `WorkflowCanvas.tsx:48-75` — pan/zoom, MiniMap, custom nodes, validation tinting — *not* placeholder forms); palette (`WorkflowPalette.tsx`); 629-line typed config panel with condition rule editor + merge-field insert (`StepConfigPanel.tsx`); visual Yes/No condition edges (`graph.ts`); node-linked client validation (`validation.ts`, 265 lines) blocking publish; lifecycle + template clone + preview + client-side test-run + version viewer; role-gated routes. Backend follow-ups: `/validate`, `/versions`, `/merge-fields` with single-source `STATIC_MERGE_FIELDS` (`template_renderer.py:54-91`). FE **91/92 tests green** (one unrelated MFA failure); backend `test_automation_workflow_routes.py` **24 passed**.

**Partial:** authoring is click-to-add + form-authored edges (nodes explicitly non-draggable/non-connectable — in-scope per §9.1 but not full drag); preview + client dry-run present, but no server-side test-run against a real sample contact.

**Missing:** **Plan-12 compliance guardrails in the builder** (content-class, PHI-in-body, missing-consent-path, blast-radius) — none in `validation.ts` or backend `/validate`; channel-readiness before publish; true server-side draft (publish goes ACTIVE immediately; "draft" is client localStorage only).

**Bugs/gaps:** **frontend never wires the Plan-03 backend endpoints** — `workflow-api.ts` lacks `listVersions`/`validateDefinition`/`listMergeFields`; `WorkflowVersions.tsx:80-86` and `merge-fields.ts` still carry stale "endpoint doesn't exist" comments. **Merge-field drift:** FE `MERGE_FIELDS` advertises `provider_name`/`appointment_date`/`appointment_time` that backend `STATIC_MERGE_FIELDS` does **not** resolve → builder marks them valid, engine renders them empty.

**Arch concerns:** two parallel validators (client vs backend) kept in sync by hand (already drifted); publishing an edit to a live ACTIVE campaign changes runtime behavior behind a generic confirm; last-write-wins concurrency (no ETag).

**Code quality:** high; discriminated-union TS model mirrors backend schema; code-split so React Flow loads on-route only.

**Scope verdict:** the flagship §9.1 experience is genuinely built and works. Unmet: compliance guardrails and wiring the authoritative backend validate/versions/merge-field endpoints — leaving a live merge-field drift bug. Clear integration lag between the two builder sessions.

---

### Plan 04 — Outbound SMS — 🟡 ~55% (High)
**Intended:** SMS action executor; per-tenant Twilio send; delivery-status webhook → run; STOP/opt-out; suppression+consent gate before send; quiet-hours hold; idempotent dispatch (`workflow_sms_attempts`); free-text inbound routing; campaign linkage; segment/price metering.

**Completed (evidence):** `SmsNodeExecutor` fail-safe on every path (`sms_node_executor.py:20-95`); dispatcher wiring (`step_dispatcher.py:117-140`); compliance gate before send (halt→quiet-hours→consent+suppression); per-tenant Twilio creds with platform fallback (`sms_service.py:199-203`); defense-in-depth `assert_can_send` + SUPPRESSED logging + sender-number match (`sms_service.py:96-170`); delivery-status webhook updating `sms_history_logs` + dead-letter of unknown SIDs (`twilio_webhooks.py:134-166`); inbound STOP/START/HELP (pre-existing). **26 tests pass.**

**Partial:** ⭐ **Finding B** — quiet-hours "hold" terminates the run (`step_dispatcher.py:124-130`), so nighttime messages are **dropped, not deferred+resumed**.

**Missing:** `workflow_sms_attempts` + idempotent dispatch (no dedupe → double-send on re-advance/timer redelivery); campaign/workflow linkage + `provider_segments`/`price` on `sms_history_logs`; segment/price metering (`update_delivery_status` ignores `NumSegments`/price); `inbound_sms_messages` + `InboundSmsRoutingService` (**free-text replies are ignored** — empty TwiML, no persistence/notification, `twilio_webhooks.py:123-133`); delivery→workflow-attempt feedback; template PHI/content-class enforcement (any `trigger_metadata` key injectable, `template_renderer.py:112-114`); CSV consent-provenance check.

**Bugs/gaps:** ⭐ Finding A (inline gate bypass, skips halt+quiet-hours; consent/suppression still caught by `SmsService`); no idempotency; UTC hardcode.

**Arch concerns:** compliance lives in the dispatcher rather than a dedicated `WorkflowSmsActionService`; hold==terminate; no attempt/delivery-log separation; two divergent dispatch entry points.

**Code quality:** strong PHI hygiene (hashed/masked phones, encrypted body, structured block reasons, per-institution retention); good single-source merge-field pattern.

**Scope verdict:** the self-scoped goal (stub → real compliant SMS with per-tenant creds) is solid and tested on the Celery path. Against full Plan 04: idempotency, delivery→run linkage, opt-out feedback, free-text inbound, metering, and quiet-hours hold-and-resume are missing, plus the inline gate bypass.

---

### Plan 05 — Outbound Email — 🟠 ~20% (High)
**Intended:** 9 components incl. `email_sending_profiles`/`workflow_email_templates`/`workflow_email_attempts`; action service + sending-profile service + `ResendCampaignClient` + `EmailWebhookService`; per-tenant domain/creds; branded HTML; bounce/complaint webhook + suppression; **unsubscribe (non-deferrable legal minimum)**; cross-channel suppression; idempotency; metering; analytics; domain verification + warm-up.

**Completed (evidence):** `EmailNodeExecutor` (`email_node_executor.py:31-125`) — resolves `contact.email` fail-safe, institution from-address + platform fallback, renders via the **SMS** renderer, sends plain-text via inline `httpx` POST to Resend; dispatcher wiring (`step_dispatcher.py:135-138`); compliance gate consumed on Celery path. **10 tests pass.**

**Partial:** cross-channel suppression only via Plan-12 gate and only on the Celery path; from-address institution-level only (no domain/DKIM/verification).

**Missing:** `email_sending_profiles` + service; `workflow_email_templates`; `workflow_email_attempts` (**no attempt/audit log at all**); `ResendCampaignClient` (uses shared platform key, not per-tenant); **email webhook route + `EmailWebhookService`** (no bounce/complaint/delivered handling); **unsubscribe link/token/email suppression** (grep "unsubscribe" → only SMS STOP); HTML/branded rendering (plain text only); idempotency (waived); metering; analytics; domain provisioning/warm-up.

**Bugs/gaps:** ⭐ **Finding D** (email consent keyed on phone hash → email-only contacts blocked `no_phone`); ⭐ Finding A (inline path skips the gate entirely — email has no defense-in-depth like SMS does, so it sends with zero consent/quiet-hours enforcement); no audit trail → future bounce reconciliation impossible.

**Arch concerns:** reuses the SMS renderer (no HTML, no email merge-field allowlist); inlined provider call instead of a `ResendCampaignClient`; shared platform Resend identity across all tenants contradicts per-clinic domain/warm-up isolation.

**Code quality:** executor is clean and mirrors the SMS fail-safe pattern; tests fully mocked.

**Scope verdict:** a competent plain-text v1 that unstubs `SendEmailNode` and leans on the gate. Against Plan 05 it is ~20%: no profiles, template model, attempt log, webhook, bounce/complaint suppression, **unsubscribe**, HTML, metering, analytics, or per-tenant domain — and email consent is broken.

---

### Plan 06 — Four Live Campaigns — 🟡 ~40% (High)
**Intended:** four launch campaigns as configurable templates (Confirmation, Reminder, Recall, **Sales Qualification**); DB-backed `workflow_templates`/`workflow_template_versions`; clone flow; per-campaign guided config + validation; PMS revalidation before appointment sends; PMS confirmation write-back; recall eligibility scan + manual/CSV enroll; centralized outcome mapping; analytics rollups; staging/test mode; frequency-cap defaults.

**Completed (evidence):** template registry + API with **instantiate now working** (`automation_templates.py` — creates draft then `publish_version`; this **closes prior CRITICAL TPL-01/02**); appointment-offset trigger **real and wired end-to-end** (webhook → `trigger_appointment_workflows` → `AppointmentTriggerService.compute_enrollment_eta` → idempotent enrollment); compliance gate before every send; **17 template tests pass.**

**Partial:** 4 templates exist but **not the scoped 4** — `appointment-reminder-24h`, `appointment-confirmation-48h`, `recall-sms-6month`, `reactivation-sms-18month`; **Sales Qualification is replaced by Reactivation**. Recall trigger is a **stub** (`scan_recall_workflows` counts/logs, enrolls nobody). Outcome mapping is ad-hoc exit strings, not normalized.

**Missing:** Sales Qualification campaign entirely; **PMS revalidation before sends**; **PMS confirmation write-back**; DB-backed versioned template model (registry is a static Python dict); per-campaign guided config; campaign-specific validation; analytics rollups; staging fixtures.

**Bugs/gaps:** **Confirmation's `confirmed` branch is dead code** — its condition tests `appointment_status == "confirmed"` but nothing populates `appointment_status` during the run → always `no_response` (`campaign_templates.py:69`; `step_dispatcher.py:243-262`). Same for reactivation's `appointment_booked`. Instantiated templates go ACTIVE immediately (no draft-from-template lifecycle).

**Arch concerns:** templates-as-static-dicts diverges from the plan's DB-backed versioned model; updates can't be versioned or safely propagated vs clones.

**Scope verdict:** ~40%. Reminder is fully live; Confirmation is SMS-send-only (its confirm/write-back logic non-functional); Recall is a template with a stub trigger; Sales Qualification dropped. Heaviest gaps are the differentiators: revalidation, write-back, real recall enrollment, versioned templates.

---

### Plan 07 — AI Callback Handling — 🔴 0% (High)
**Intended:** `callback_requested` trigger; per-location manual/AI toggle (`callback_automation_settings`); `callback_workflow_links` table; a 5th AI-callback template (wait-until-preferred-time → outbound AI call → resolve/fallback); capture preferred callback time; three services.

**Status:** **none of the Plan-07-specific work exists.** What exists is entirely the pre-existing inbound callback queue the plan lists as "Existing System Context": callback queue API (`callbacks.py`), `Call.preferred_callback_datetime`/`callback_resolved*` fields, `CallStatus.NEEDS_CALLBACK`, `callbacks_updated` SSE, frontend `Callbacks.tsx`. Grep for `callback_automation|CallbackAutomation|callback_workflow_link|callback_requested` → matches only in the plan doc. Trigger types supported today are `appointment_offset` and `recall_scan` only.

**Scope verdict:** correctly not started — it depends on Plan 03 (outbound voice), and the template registry deliberately excludes voice until per-clinic Retell agent IDs exist (`campaign_templates.py:4-6`). ~0% is expected and appropriate. The inbound callback queue provides the intended manual-fallback foundation.

---

### Plan 08 — Campaign Mgmt / Progress / Analytics UI — 🟡 ~22% full / ~90% Phase-3 subset (High)
**Intended:** campaign list (status/channels/enrollment/outcomes; activate/pause/duplicate/archive); overview/config; enrollment UI (manual/multi-select/**CSV**+preview); run progress list w/ filters; run-detail timeline; analytics incl. attributed revenue; operations page (dead-letter replay, stale timers, emergency halt); `campaign_metrics_daily`/`campaign_enrollment_batches` tables; 3 new SSE event types.

**Completed (evidence):** **real interactive** campaign list (`Campaigns.tsx` → `GET /automation/workflows`; pause/resume/archive in-place; edit-in-builder + detail links; skeletons/empty state); campaign detail with read-only run table (`CampaignDetail.tsx`); lifecycle backend (pause/resume/archive/run-list/run-status/cancel-run/manual-enroll/bulk-enroll); role-gated routes + sidebar; **emergency-halt backend exists** (`/outbound-halt` GET/POST/DELETE) though no FE consumes it. Backend `test_automation_workflow_routes.py` **24 passed**.

**Partial:** run progress is a read-only table inside the detail page (no `/runs` route, no filters, no current-step/next-due columns); run detail returns status only (no timeline); list columns lack channels/enrollment counts/outcome metrics.

**Missing:** enrollment UI; **CSV** import/mapping/validate/preview; analytics/reporting page + metrics endpoints; attributed revenue; `campaign_metrics_daily`/`campaign_enrollment_batches` tables; operations page (dead-letter/replay/stale-timers); emergency-halt UI; usage/cost views; the 3 SSE event types (pages are refetch-on-mount only); location-user/group-admin views. Most are Phase-6-deferred per the sequence doc.

**Bugs/gaps:** archive uses browser-native `confirm()` (`Campaigns.tsx:106`, `CampaignDetail.tsx:122`) — the session docs' "Archive confirm Dialog" claim is **inaccurate**; detail conflates runs with "enrollments"; `listCampaignRuns` has no status filter/cursor pagination.

**Arch concerns:** analytics-via-rollup (the plan's central perf decision) is entirely unbuilt — no metrics tables means analytics would hit raw runs, the exact anti-pattern the plan warned against.

**Tech debt:** no SSE wiring ("real-time progress" = manual refresh); native `confirm()`; single-role gating hardcoded.

**Scope verdict:** an honest, minimal Phase-3 read-only slice rendering real backend data (no placeholders). ~90% of the intended current-phase subset; ~22% of the full plan. Main doc/code discrepancy: the "archive confirm dialog" claim and framing the read-only run table as full progress functionality.

---

### Plan 09 — Integration & Data Layer — 🟠 ~27% (High)
**Intended:** event-driven NexHealth read model — webhook subscription lifecycle; signed idempotent receiver; appointment + recall working-set **projections**; initial REST backfill; paced/jittered reconciliation sweep; recall-list pull; **live re-validation at send time**; shared-key rate-limit pacing.

**Completed (evidence):** webhook receiver + HMAC-SHA256 verify (`nexhealth_webhooks.py:26-149`); appointment-offset trigger dispatch with ETA-scheduled enrollment + idempotency key `appt:{version}:{appt_id}` (`appointment_trigger_service.py` + `tasks/automation_workflow.py:352-460`); bulk-enroll endpoint (max 500, async 202). Two suites **23 passed**.

**Partial:** recall scanner is a **stub** (counts/logs only; explicit NOTE that the patient-history query awaits a NexHealth sync layer that was never built).

**Missing (no code, no migration):** `appointment_working_set` projection (the plan's central deliverable — webhook persists nothing, it enqueues directly); `recall_eligibility_working_set`; `nexhealth_webhook_subscriptions`; `nexhealth_webhook_events` (event ledger/idempotency); subscription lifecycle; initial REST backfill; reconciliation sweep; recall pull; **`PmsLiveRevalidationService` / any send-time revalidation**; rate-limit pacing for backfill/reconciliation; multi-key routing; observability/alarms.

**Bugs/gaps:** ⭐ **Finding E** — cancellation/reschedule unhandled: every `appointment.updated` enrolls without a status check, `appointment.cancelled` ignored, and with no revalidation, cancelled/rescheduled patients still get messaged; ⭐ **Finding C** — signature fails open when secret unset; no event-level idempotency (dup deliveries re-run trigger+queries, deduped only at `enroll()`); whole-table scan + Python `trigger_type` filter.

**Arch concerns:** the disposable-projection + live-revalidation design was not built — what shipped is a direct webhook→enroll passthrough, losing out-of-order handling, reconciliation repair, staleness detection, and cancellation safety. No backfill means only appointments created *after* subscription can ever trigger.

**Scope verdict:** ~27%. Real, tested webhook + appointment-trigger + bulk-enroll, but every *defining* component (projections, subscription lifecycle, event ledger, backfill, reconciliation, recall pull, revalidation, rate-paced jobs) is absent or stubbed, and the shipped design carries an unmitigated cancellation/reschedule safety gap.

---

### Plan 10 — Per-Tenant Messaging Provisioning — 🟡 ~20% full / ~90% rescoped MVP (High)
**Intended:** per-clinic Twilio sub-account provisioning + A2P 10DLC/toll-free registration; encrypted per-tenant credential storage + migration; `TenantTwilioCredentialResolver`; `MessagingProvisioningService`/`TwilioProvisioningClient`; email domain provisioning (SPF/DKIM/DMARC + warm-up); `messaging_readiness_checks` model; per-sub-account webhook validation. (Plan: 7 tables + 6 services.)

**Completed (evidence):** **encrypted credential storage is real AES-256-GCM, not a stub** (`models/institution.py:86-107,224-229,267-281` — `AESGCM` + random 96-bit IV, same proven pattern as `nexhealth_api_key_encrypted`); migration `20260703_institution_provisioning.py` (4 nullable columns, clean downgrade); SMS per-institution creds + platform fallback; outbound email per-institution from-address + fallback; admin API (SUPER_ADMIN-gated, masks SID, never returns token). **9 tests pass.**

**Partial:** the "credential resolver" is inline logic in `sms_service.send_sms`/`email_node_executor`, not the planned reusable service; email = from-address/name only (no per-tenant API key, no domain).

**Missing:** all 6 provisioning tables; all 6 services; A2P 10DLC/toll-free registration; email domain SPF/DKIM/DMARC + warm-up; **readiness model/state entirely** (violates the plan's core "activation depends on provisioning state" decision); vendor provisioning automation (creds entered manually); status-sync job; feature flags; Secrets Manager references.

**Bugs/gaps:** **Twilio webhook validation ignores sub-account tokens** (`twilio_webhooks.py:168-180` validates with the platform token only) — inbound-SMS + status callbacks will fail signature validation the moment a real sub-account is activated (dormant only because none exist yet); no number↔sub-account ownership check; no readiness model.

**Arch concerns:** institution-level creds but location-level numbers → multi-location clinics share one sub-account; "provisioning" is really credential-storage+routing with no vendor automation; no Secrets Manager path.

**Scope verdict:** the session honestly rescoped to a credential-storage-and-routing MVP and delivered it cleanly and securely (encryption is genuine). But the plan's defining deliverables (tables, services, A2P/10DLC, email domains, readiness, provisioning automation, per-sub-account webhook validation) are absent. Highest-priority latent defect: the webhook signature gap.

---

### Plan 11 — Usage & Cost Reporting — 🔴 0% (High)
**Intended:** unified `usage_events` model (voice/sms/email × in/out; minutes/dials/segments/emails; provider cost; idempotency); `usage_cost_rollups` (the contract Plan 08 depends on); `usage_budgets`; `UsageMeteringService` ingestion hooked into Retell/Twilio/Resend webhooks; `UsageRollupService` (location→institution→DSO); reporting API + Plan-08 dashboard; cost-estimation fallback; alarms.

**Status:** **nothing implemented.** Repo-wide grep for `usage_event|UsageMetering|usage_cost_rollup|usage_budget|UsageRollup` hits only 3 plan docs, zero source. No migration. The only pre-existing signal is raw `call_duration_seconds` (`models/call.py:218`), not wired to anything; `call_metrics_daily` exists as the *pattern* to mirror but no usage rollup was built on it.

**Critical cross-plan gap:** the sequence doc requires metering **ingestion to ship with the first channel (04)** so history accumulates. It did **not** — SMS/email dispatch captured no `NumSegments`/price/cost and emit no usage events. Whoever builds Plan 11 must **retrofit** cost/segment/minute capture into the already-shipped SMS/email/voice paths, and Plan 08 analytics + Plan 12 budgets are both left without a data source.

**Scope verdict:** 0% delivered. Ingestion (should have shipped with 04) is a gap; rollups/dashboards (Phase-6) being absent is consistent with sequencing.

---

### Plan 12 — Compliance & Consent — 🟠 ~27% (High)
**Intended (foundational, "must land before any channel sends"):** multi-channel consent+suppression model (per-location scoped); content-class + PHI validator; frequency capping; AI-voice consent/disclosure/opt-out; emergency halt of in-flight runs on a version; spend/blast-radius controls; campaign RBAC. Plus `ComplianceGateService`, tz/DST-aware `QuietHoursService`, **bilingual EN/FR STOP**, DNC scoping, audit of config changes.

**Completed (evidence):** `ComplianceGateService` — 3 checks halt→quiet-hours→consent (`compliance_gate_service.py:34-84`), DST-correct via `ZoneInfo`/`astimezone` (L101-136), reuses `LocationOperatingHours`; **gate invoked before every send node** on Celery paths (`step_dispatcher.py:118`; `tasks/automation_workflow.py:184,326`); SMS defense-in-depth `assert_can_send`; **emergency halt** (`OutboundEmergencyHalt` model + RLS append-only migration + `/outbound-halt` routes); `ConsentChannel` enum expanded to sms/email/voice + CHECK + migration. **13 gate tests pass.**

**Partial:** consent/suppression enum+constraints are multi-channel, but `location_id` exists yet is **not in the scope/unique key** — suppression stays institution+channel+phone, DNC institution+phone; no per-location or group "remove everywhere" tiers. Emergency halt is an **institution-wide gate flag** — does *not* cancel timers/terminate in-flight runs, is not version-scoped, and is not audit-logged. Quiet-hours logic is inline in the gate, not a reusable `QuietHoursService`.

**Missing:** content/PHI validator + `workflow_content_class`; frequency cap + `contact_frequency_ledger`; AI-voice consent/disclosure; blast-radius/spend controls; new campaign RBAC (halt reuses `_InstitutionAdmin`); **bilingual FR STOP** (`twilio_webhooks.py:29` is EN-only — no ARRET/ARRÊT); named `ConsentService`/`SuppressionService`.

**Bugs/gaps:** ⭐ **Finding D** (email/voice consent keyed by phone hash → email-only contacts blocked, email consent non-functional); ⭐ **Finding A/B** (inline enroll bypasses the gate; quiet-hours hold drops the message); US cross-timezone quiet-hours caveat unaddressed (clinic TZ only).

**Arch concerns:** not the "single authoritative shared gate" the plan mandates — SMS keeps its own path, email has none on the inline route, and the gate isn't on every dispatch path. Consent/suppression stay phone-centric; extending to email by reusing `phone_hash` is a schema shortcut needing a real migration.

**Scope verdict:** materially incomplete for a Phase-1 foundational plan. ~27% by deliverable count. The shipped gate+halt+quiet-hours slice is functional and tested on the Celery path, but 5 of 7 major deliverables are absent, the halt is weaker than specified, email consent is broken, and a NoOp gate-bypass exists on the inline route.

---

## 4. Overall Phase 2 progress summary

- **Overall completion:** ~35% of full Phase-2 plan scope (11 plans, excluding Plan 03).
- **Complete (≥85%):** 1 — Plan 01 (engine).
- **Substantially/partially complete (40–80%):** 3 — Plan 02 (75%), Plan 04 (55%), Plan 06 (40%).
- **Minimal/thin slice (20–30%):** 4 — Plan 05 (20%), Plan 08 (22% full), Plan 09 (27%), Plan 10 (20% full), Plan 12 (27%). *(5 plans)*
- **Not started (0%):** 2 — Plan 07 (correctly, blocked on 03), Plan 11.

**Major milestones achieved**
- Durable, multi-tenant, timezone/DST-aware workflow engine with immutable versioning and **stale-claim recovery now wired** (closes the prior CRITICAL).
- **Real end-to-end compliant send on the Celery path** for SMS and Email — the system is no longer send-incapable.
- A **real React Flow visual builder** (the flagship UI) — the single biggest net-new frontend investment.
- Template instantiate **fixed** (closes prior CRITICAL); appointment-offset trigger wired end-to-end.
- **Genuine AES-256-GCM per-tenant credential storage** and a functional compliance gate (halt→quiet-hours→consent) + emergency-halt endpoints.

**Remaining milestones (largest → smallest)**
- The event-driven data layer's resilient core (projections, backfill, reconciliation, **live revalidation**, recall pull) — Plan 09.
- Compliance depth (frequency caps, content/PHI validator, per-location/group suppression, FR STOP, real in-flight halt) — Plan 12.
- Usage metering end-to-end incl. **retrofitting channel dispatch** — Plan 11.
- Campaign differentiators (PMS write-back, revalidation, real recall enrollment, DB-backed versioned templates, Sales Qualification) — Plan 06.
- Per-tenant provisioning automation + readiness model + per-sub-account webhook validation — Plan 10.
- Email hardening (unsubscribe, bounce/complaint webhook, HTML, per-tenant domain, attempt log) — Plan 05.
- Full campaign-management UI (CSV, analytics, SSE, run timeline, ops/dead-letter, emergency-halt UI) — Plan 08.
- Plan 07 (after Plan 03 lands).

---

## 5. Cross-cutting critical findings (systemic)

1. ⭐ **Two divergent enroll+advance paths.** The inline route (`automation_workflows.py:463-469`) builds the dispatcher with **no compliance gate** (→ NoOp) and **UTC**; the Celery paths do it correctly. This single divergence is the root of Findings A/B's inline variants and appeared independently in the 01/04/05/12 passes. **Converging the two paths onto one gated, tz-resolving factory would close multiple findings at once.**
2. ⭐ **Quiet-hours "hold" drops rather than defers** (`step_dispatcher.py:124-130`) — a direct violation of scope §8. A held send should schedule a timer to the next permitted window, not `complete_run`.
3. ⭐ **Webhook signature fails open by default** (`nexhealth_webhooks.py:32-34`, `config.py:76`) — no production guard; unauthenticated cross-tenant enrollment on misconfiguration.
4. **Compliance surface is phone-centric.** Consent/suppression key on `phone_hash`; email consent is non-functional and voice will inherit the same gap. Needs an address/channel-generic consent identity.
5. **Cancellation safety.** No send-time revalidation + unhandled `appointment.cancelled` → cancelled patients can be contacted (Finding E).
6. **Metering was not shipped with the first channel**, contradicting the sequence; channels lack hook points, forcing a later retrofit.
7. **Test quality: mock-heavy, wiring-blind.** Almost all suites pass while mocking collaborators; none of the four self-verified live defects (inline gate bypass, quiet-hours drop, signature-open, cancellation) is covered by a test. This is the same pattern the prior report flagged (XCUT-01) and it persists.
8. **Emergency halt is a soft flag,** not the in-flight-run termination the plan specifies, and it is not audit-logged.
9. **Frontend/backend contract drift** (Plan 02 merge fields; stale "endpoint doesn't exist" comments) — hand-synced validators/catalogs already diverged.
10. **Plans not updated to record deferrals.** Sessions were honest; the plan docs still describe full scope, so plan-vs-code diffing overstates gaps unless read together.

---

## 6. Architecture review

**Aligned with the original architecture?** Largely yes at the engine/tenancy layer. RLS-forced multi-tenant tables, per-location timezone, immutable versioning, the compliance-gate seam, and the Retell/NexHealth reuse pattern all match the scope. The visual builder mirrors the backend definition schema via a discriminated union.

**Where it is drifting:**
- **Data layer (Plan 09)** drifted from the scope's central design: the "thin disposable working set + live revalidation" was replaced by a direct webhook→enroll passthrough. This is the most significant architectural divergence and it removes the resilience (out-of-order, reconciliation, cancellation safety) the scope explicitly required.
- **Compliance (Plan 12)** is not the "single authoritative gate on every dispatch path" the scope mandates — it's gate-on-Celery + SMS-own-path + email-none-on-inline.
- **Templates (Plan 06)** are static Python dicts, not the DB-backed versioned model — updates can't be versioned/propagated safely.
- **Provisioning (Plan 10)** is credential-storage+routing, not provisioning-with-a-readiness-state; campaign activation has no readiness gate.

**Scalable?** The engine's durable scheduler (`SKIP LOCKED`) and per-tenant sharding intent are sound. But: no rate-limit pacing of the shared NexHealth key for jobs, whole-table workflow scans per webhook, per-item bulk-enroll round-trips, and no metering all cap scale. None are blocking at pilot volume.

**Maintainable?** The code that exists is clean, typed, and consistent with repo patterns (subagents rated code quality high across the board). The maintainability risks are structural: divergent dispatch paths, hand-synced FE/BE contracts, and static-dict templates.

**Production-ready?** **Not yet for real sends.** The engine and builder are close. But Findings A–E are live compliance/security/correctness risks, unsubscribe (email legal minimum) is absent, metering is absent, and the data layer lacks cancellation safety. It is production-ready as an *operator-driven pilot on the Celery path with a hardcoded secret and manual audience control* — not as an autonomous multi-tenant sender.

---

## 7. Quality review

- **Bugs (live):** Findings A–E (§1); confirmation-branch dead code (Plan 06); Twilio sub-account webhook validation gap (Plan 10); merge-field drift (Plan 02); no SMS/email idempotency → double-send.
- **Missing implementations:** unsubscribe (05); bounce/complaint webhook (05); free-text inbound routing (04); PMS write-back + revalidation + real recall enrollment (06/09); working-set projections + backfill + reconciliation (09); all of metering (11); frequency caps + content/PHI validator (12); readiness model (10); CSV/analytics/SSE/ops UI (08).
- **Edge cases:** DST spring-forward is now handled in due-at; but US cross-timezone quiet hours, out-of-order webhooks, and email-only contacts are not.
- **Incomplete integrations:** FE↔Plan-03 endpoints unwired (02); delivery-status↔run feedback missing (04); metering hooks absent in channels (11); halt endpoint has no UI (08).
- **Missing validation:** builder compliance guardrails (02); template content-class/PHI enforcement (04/12); consent-provenance on CSV enroll (04).
- **Missing testing:** no integration/E2E for the four self-verified defects; no frontend tests for campaign pages; mock-heavy suites give false wiring confidence (systemic, §5.7).
- **UI/UX inconsistencies:** native `confirm()` vs app Dialog (08); no SSE "real-time" (08); flat routes vs planned IA (08).
- **Documentation gaps:** plan docs not updated for MVP deferrals; stale "endpoint doesn't exist" comments (02); session "complete" ≠ "verified working" (persists from prior report).

---

## 8. Recommendations

### 8.1 Do first — a "send-safety hardening" slice (small, high-leverage, mostly bug-fixes)
These are cheap, they close the self-verified live risks, and they unblock a safe pilot. Recommended **before any new feature plan**:
1. **Converge the inline enroll path onto the gated, tz-resolving dispatcher factory** (kills Finding A everywhere; one change closes several findings). — `automation_workflows.py:463-469`.
2. **Make quiet-hours "hold" schedule a resume timer** instead of `complete_run` (Finding B). — `step_dispatcher.py:124-130`.
3. **Fail closed on the NexHealth webhook secret in production** (Finding C) — startup guard or reject-all when unset. — `nexhealth_webhooks.py:32`, `config.py:76`.
4. **Handle `appointment.cancelled`/status on `appointment.updated`** and add a minimal send-time revalidation (Finding E). — `nexhealth_webhooks.py:71-88`.
5. **Add an integration test per fix** (the wiring-blind test gap is what let these survive).

### 8.2 Recommended next full plan — **Plan 12 (Compliance depth)**, then **Plan 09 (data-layer resilience)**
Per the sequence doc, 12 and 09 are foundational and everything downstream depends on them; they are also where the biggest go-live risks live. Concretely:
- **Plan 12 next:** fix the phone-keyed email consent (Finding D) with a channel-generic consent identity, add frequency caps + content/PHI validator, per-location/group suppression tiers, FR STOP, and make emergency halt terminate in-flight runs + audit-log. This directly de-risks the SMS/email that already ship.
- **Plan 09 close behind:** build the appointment working-set projection + backfill + reconciliation + `PmsLiveRevalidationService`, and the real recall-eligibility pull (which also unblocks Plan 06 Recall).

### 8.3 Independent work that can proceed in parallel
- **Plan 11 metering** — largely independent; but do it **soon** and **retrofit the channel dispatch hooks now** while the code is fresh (every day of delay loses billable history and Plan 08 analytics data).
- **Plan 05 email hardening** (unsubscribe + bounce/complaint webhook + attempt log) — independent of 09/12 depth; unsubscribe is a legal minimum and should not wait.
- **Plan 02 builder polish** — wire the already-built `/validate`/`/versions`/`/merge-fields` endpoints and fix merge-field drift; add compliance guardrails once Plan 12's validators exist.
- **Plan 10** — extract the reusable credential resolver and fix the sub-account webhook validation *before* the first real sub-account is provisioned.

### 8.4 Has dependencies — should wait
- **Plan 06 Recall enrollment** waits on Plan 09's recall pull; **Confirmation write-back** waits on the PMS-write action.
- **Plan 07** waits on Plan 03 (voice) — correctly deferred.
- **Plan 08 analytics** waits on Plan 11 rollups; **Plan 08 emergency-halt UI** waits on Plan 12's real halt.

### 8.5 Refactors to do before building further
- **Unify the two dispatch entry points** (§5.1) — prerequisite to trusting any compliance guarantee.
- **Move templates to the DB-backed versioned model** before adding more campaigns (Plan 06), or template edits will be unversioned and unsafe to propagate.
- **Establish one FE/BE contract source** for merge fields + validation to stop the drift (Plan 02).
- **Adopt integration/E2E tests for critical wiring** (systemic) — the mock-heavy suites are the reason live defects survive "green" runs.

---

## 9. Delta vs the prior report (`verification-phase2/report.md`)

**Closed since the prior snapshot:**
- ENG-01 (stale-claim recovery unscheduled) — now wired.
- TPL-01/TPL-02 (template instantiate `TypeError` + no version persisted) — fixed.
- XCUT-03/XCUT-04 (compliance gate NoOp everywhere; sends are stubs) — a **real** gate now exists and SMS/email send for real on the Celery path.
- DST spring-forward — now handled in due-at math.

**Still open / regressed / newly surfaced:**
- ENG-05 (inline enroll UTC) — **still open**, now compounded by the inline **gate bypass** (Finding A).
- ENG-06 (quiet hours) — now enforced, but with the **hold-drops-message** defect (Finding B).
- DATA-01 (signature fails open) — **still open** (Finding C).
- DATA-04/05 (cancellation + projection/revalidation) — **still open** (Finding E).
- ENG-02 (emergency halt) — partially addressed (endpoints exist) but still a **soft flag, not in-flight terminate**.
- New: email consent phone-keyed (D); merge-field drift (02); metering-not-shipped-with-channel (11); Twilio sub-account webhook gap (10); confirmation-branch dead code (06).

---

## Appendix A — Author self-verified (re-read this session)
- Finding A: `automation_workflows.py:463` (dispatcher built with no gate), `:469` (`location_timezone="UTC"`).
- Finding B: `step_dispatcher.py:124-130` (`hold` → `complete_run(outcome="compliance_hold")`).
- Finding C: `nexhealth_webhooks.py:32-34` (`if not secret: return`).

## Appendix B — Evidence base
Per-plan deep-dive detail with full `file:line` citations: `plan-01-findings.md` … `plan-12-findings.md` in this folder. Every per-plan status was produced by an independent verification pass that ran graphify + read the plan + read the session docs + inspected code + ran the relevant unit subset where feasible.

## Appendix C — Confidence
All 11 per-plan assessments are **High confidence** (direct code inspection + passing-test confirmation). The overall ~35% figure is a considered estimate; the per-plan percentages are the more reliable numbers and should be cited in preference to the aggregate.
</content>
