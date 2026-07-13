# Verification v3 — Raw per-plan agent returns

Each block is the verbatim structured return from that plan's read-only verifier subagent.

---

## Plan 01 — Workflow Engine — VERDICT: Substantially complete & production-grade; only defect is dead extensibility scaffolding

### Bucket 1 — Complete
- Single gated dispatch factory `build_dispatcher` — real gate + resolved tz; ONLY construction path (inline + all 4 Celery sites) — step_dispatcher.py:395-437; callers automation_workflows.py:735, tasks/automation_workflow.py:239,453,982,1232.
- Quiet-hours hold-and-resume (held, never dropped) — step_dispatcher.py:170-198 + 328 + resume_after_timer:257-333.
- content_class threaded into gate — step_dispatcher.py:161-164.
- Dispatch-time revalidation seam — step_dispatcher.py:146-159.
- Emergency halt (version + institution scope) terminates in-flight runs + cancels timers — definition_service.py:247-301; routes automation_workflows.py:901,466,514.
- Route ordering correct (literal before /{workflow_id}) — automation_workflows.py:430,465,531 vs 562.
- FOR UPDATE SKIP LOCKED claiming — scheduler_service.py:83; claim TTL 120s (L19).
- Stale-claim recovery wired to beat — scheduler_service.py:141-160; task tasks/automation_workflow.py:274-301; beat 60s<120s, poll 30s (worker.py:57-81).
- Concurrency-safe enrollment idempotency (unique idx + IntegrityError re-lookup) — enrollment_service.py:59-113,146-160.
- Immutable versioned definitions + in-flight version pinning — definition_service.py:129-206; tested integration:167-190.
- Fail-closed publish validation — definition_service.py:157-162.
- Calendar jitter — step_dispatcher.py:117-121 (default 300s).
- Dead-letter — tasks/automation_workflow.py:44,166-169,386.
- CloudWatch metrics emitter — tasks/automation_workflow.py:311; worker.py:82-87.
- SSE progress events — runtime_service.py:257.
- Pause-defer via reschedule_timer — scheduler_service.py:124-139, used tasks/automation_workflow.py:215.
- Voice outcome-park loop integrated — step_dispatcher.py:209-231,318-327.

### Bucket 2 — Incomplete
- Paced/budget-aware dispatch vs shared NexHealth key — only jitter ships (step_dispatcher.py:117-121); no global rate limiter. Non-cap smoothing, not a blocker.
- Real vendor send paths remain mock-only (XC-2) — not an engine gap per se.

### Bucket 3 — Unnecessary / should be removed
- `register_action_executor` — defined, never called; registration is the module-level dict `_ACTION_EXECUTORS` — action_registry.py:43-45 — HIGH.
- `supported_action_types()` — never called — action_registry.py:53-54 — HIGH.
- `SUPPORTED_TRIGGER_TYPES` — defined, never referenced — action_registry.py:57-60 — HIGH.
- `_dispatch_send_stub` — unreachable for the 3 real send node types (all registered) — step_dispatcher.py:203-204,335-347 — MED (keep as guard or delete with registry seam).

### Bucket 4 — Missing / genuinely required
- None blocking. Paced dispatch (B2) is the only real shortfall; non-cap, correctly deferred.

### Report challenges
- Stale-claim recovery cited "worker.py:56-77"; actual beat entry worker.py:76-81. Wiring real.
- Hold-and-resume cited "165-193"; actual hold branch 170-198. Behavior confirmed.
- build_dispatcher "395-437" — exactly correct.
- Report never flags the dead registry seam (its "action/trigger registries" bullet implies live features; they're half-dead). Blind spot.
- "100%" defensible for function but overstates given paced-dispatch=jitter-only + unused registry scaffolding.

### Test coverage
- integration test_automation_engine_integration.py (12 real-Postgres tests) — publish immutability/version pinning, enroll→wait→resume→exit, stale-claim recovery, emergency-halt cascade, idempotency unique idx, send idempotency+hold-resume reclaim, voice outcome resume/branch, voice attempt+stamp, voice claim blocks re-dial, RLS isolation.
- unit: step_dispatcher, scheduler_service, compliance_gate, pms_revalidation.
- Gaps: stub path untested (dead); registry seam untested (dead); paced dispatch untested (unimplemented); reschedule_timer pause-defer no dedicated integration test.

---

## Plan 02 — Visual Builder UI — VERDICT: Substantially complete & accurately reported; no dead components; ETag gap real; tests 137 not 130 (could not execute — root-owned node_modules)

### Bucket 1 — Complete
- React Flow canvas: pan/zoom (WorkflowCanvas.tsx:117), MiniMap (:129-136), custom nodeTypes (:32-35), validation tinting (WorkflowBuilder.tsx:185-188), drag-connect (:78-88,105), layout persistence (:90-95,106) + Tidy layout (:118-128); @xyflow/react ^12.11.1; read-only gating (:107-108).
- Palette wired (WorkflowBuilder.tsx:394-399).
- StepConfigPanel: condition-rule editor (StepConfigPanel.tsx:387-458), merge-field INSERT from live catalog (:496-536), preview (:32).
- Merge-field single-source: FE FALLBACK_MERGE_FIELDS (merge-fields.ts:19-24) mirrors backend STATIC_MERGE_FIELDS (template_renderer.py:54-91); live GET /merge-fields (automation_workflows.py:359-382). Drift FIXED.
- Endpoints consumed: /validate, /versions, /merge-fields, /dry-run, /channel-readiness (workflow-api.ts) — all exist/called.
- Authoritative server publish validation — WorkflowBuilder.tsx:294-304.
- Compliance guardrail panel code-AGNOSTIC (renders any issue.code) — WorkflowValidationPanel.tsx:178-180 → new Plan-12 codes surface with zero FE change.
- Compliance settings (content_class + consent_required) — ComplianceSettings.tsx:34-88.
- Server dry-run (TestRunDialog), channel-readiness warning-only (never blocks), version history (WorkflowVersions), draft autosave localStorage. All routes wired (router.tsx:275-307).

### Bucket 2 — Incomplete
- Optimistic concurrency/ETag on live-ACTIVE edit: MISSING (see B4). Generic confirm identical regardless of status (WorkflowPublishControls.tsx:84-111).

### Bucket 3 — Unnecessary / should be removed
- NONE. Every workflow component + lib imported by production code. createWorkflowFromTemplate NOT dead (WorkflowTemplates.tsx:56; backend has no /instantiate — confirmed). WorkflowStatuses/workflow-status-api are the call-disposition feature (router.tsx:264-267), not Plan 02, not dead.

### Bucket 4 — Missing / genuinely required
- ETag/optimistic concurrency on PATCH /automation/workflows/{id}: CONFIRMED ABSENT. update_workflow (automation_workflows.py:598-614) accepts no If-Match/expected_version — last-write-wins; two admins editing same live campaign → silent clobber. Real but low-frequency; report's "partial" caveat is honest.

### Report challenges
- "130 FE tests" — actual 137 it()/test(). Undercounts by 7.
- "tsc clean" + passing suite — COULD NOT verify (node_modules root-owned/empty, npm install EACCES). Plausible but unverified by execution.
- Upheld: code-agnostic compliance panel; merge-field single-source; last-write-wins caveat.

### Test coverage
- 137 FE tests (~97 cover builder). workflow-graph 28, workflow-api 14 (asserts every endpoint), workflow-validation 11, WorkflowValidationPanel 9, preview 7, test-run 6, readiness 5, merge-fields 4, etc.
- Gap: no concurrent-edit/stale-write test (consistent w/ missing ETag); no E2E/browser; execution unconfirmed.

---

## Plan 03 — Outbound Voice — VERDICT: Functionally complete & accurately reported; park→resume→branch wired & reachable; P9 claim + XC-1b timeout-terminal real; no dead fallback; no Bucket 4

### Bucket 1 — Complete
- Outcome loop end-to-end: executor returns VoiceParked (voice_node_executor.py:248-256); dispatcher parks + safety timer (step_dispatcher.py:209-230); webhook maps disconnection_reason→call_outcome + enqueues resume for OUTBOUND+retell_call_id (webhooks.py:546-572); resume task finds parked step, cancels timer, writes outcome, resumes (automation_workflow.py:915-996); advances PAST send (step_dispatcher.py:318-323); executor registered (action_registry.py:39).
- P9 crash-safe claim: INITIATING + commit BEFORE POST (voice_node_executor.py:179-182; recorder:102-127); skip-if-claimed (voice_node_executor.py:107-112; voice_send_already_claimed:48-71).
- XC-1b timeout-terminal: network/timeout→RetellAmbiguousError (retell_outbound_client.py:82-85)→no retry, claim stays INITIATING/blocking (voice_node_executor.py:212-228); timer-fired sets outcome="timeout", advances past (step_dispatcher.py:324-327); 5xx transient/4xx permanent (:88-93).
- V-4 data model: outbound_voice_profiles + workflow_voice_attempts w/ CHECK constraints, partial-unique indexes, RLS — models/outbound_voice.py:72-176; migration 20260708_voice_data_model.
- Profile override-with-fallback — voice_node_executor.py:141-152.
- V-6 transient retry via max_attempts — voice_node_executor.py:193-211; schema :150.
- V-7 client extraction; N-1 dead fallback GONE (only callsite voice_node_executor.py:186).
- V-8 API real + RBAC-registered, 409 active-per-location, masked numbers — outbound_voice.py:38-39,151-270; main.py:41,280.
- AI-call disclosure injected — voice_node_executor.py:61-72,162-174.
- Voice usage metering attributed via metadata.workflow_run_id — webhooks.py:416-439.
- Spoken opt-out WRITE+WIRING built (location-scoped DoNotContact) — voice_optout_service.py:49-92; webhooks.py:667-695.

### Bucket 2 — Incomplete
- None functional. VoiceParked.timeout_minutes hardcoded 30 (voice_node_executor.py:58) — by-design (no schema field).

### Bucket 3 — Unnecessary / correctly dormant
- Spoken-opt-out DETECTION config-gated OFF via retell_optout_analysis_key (config.py:85; returns False when unset — voice_optout_service.py:36-38). JUDGMENT: correctly dormant-until-field-exists (do-not-guess rule), NOT incomplete. Write half live+tested. Keep as-is.
- record_placed_attempt (recorder:158-181) one-shot convenience unused by executor (test-only). Harmless; leave. LOW.

### Bucket 4 — Missing / genuinely required
- None. V-8 React UI (FE lane), voice cost pricing (Plan 11), V-9 workspace isolation (infra/scale) all correctly out-of-scope.

### Report challenges
- All major claims CONFIRMED (resume reachable, P9 real, XC-1b prevents double-dial, N-1 removed).
- Caveat surfaced: spoken "stop" does NOT suppress until CTO sets retell_optout_analysis_key — real (intended) compliance dependency, disclosed.
- Doc nit: "voicemail→SMS, no-answer→retry" are per-workflow ConditionNode branches, not engine behavior.

### Test coverage
- Unit: 42 PASS locally (executor 20, optout 6, client, routes). Covers idempotency, override/fallback, park, P9 commit-before-POST, skip-when-claimed, transient/permanent/ambiguous, give-up, optout on/off.
- Integration (read-verified): park→resume→branch (no re-dial), attempt row+stamp, claim blocks re-dial, profile unique+list, consent channel-scoped — integration:375-588.
- Gap: no test asserts webhook actually enqueues resume_voice_outcome for OUTBOUND (webhooks.py:546-572 enqueue block) — verified by inspection only.

---

## Plan 04 — Outbound SMS — VERDICT: Functionally complete & production-grade; no dead/duplicate send paths; two "not-required" residuals defensible

### Bucket 1 — Complete
- SmsNodeExecutor fail-safe, registered (action_registry.py:37), reachable — sms_node_executor.py:20-104; dispatcher step_dispatcher.py:202-208.
- Send-time idempotency: already_sent guard BEFORE begin_step (sms_node_executor.py:42-48); impl runtime_service.py:105-129 keyed on COMPLETED step result_code∈{sent,call_placed}; hold step completes w/ no result_code so no false-positive. Reachable on hold→resume.
- SmsService.send_sms — sms_service.py:64-279 (sender match, gate assert_can_send, SUPPRESSED row on block, retention).
- Per-tenant Twilio creds + platform fallback — TenantTwilioCredentialResolver (sms_service.py:201); _get_twilio_client fallback (L54-62); webhook sig resolves sub-account token (twilio_webhooks.py:311-319).
- Inbound routing (S-2): InboundSmsRoutingService.record_inbound persists EVERY reply (inbound_sms_routing_service.py:30-67); contact/run correlation only when exactly one matches (L69-99); model inbound_sms_message.py; migration 20260709_inbound_sms_messages.py w/ RLS+indexes; wired twilio_webhooks.py:139-151; free-text→NotificationType.INBOUND_SMS_REPLY.
- STOP/START/HELP — twilio_webhooks.py:48-91,128-188 (keyword-anywhere, French CASL, STOP>START, audit); confirmation bare-token→resume_sms_confirmation (automation_workflow.py:1026).
- Delivery-status webhook→sms_history_logs — twilio_webhooks.py:242-292; update_delivery_status (sms_service.py:281-310); unmatched SID→dead-letter.
- Usage metering on terminal status once per msg, idempotency_key sms:{MessageSid} dedupes — twilio_webhooks.py:279-291; usage_metering_service.py:106-117.

### Bucket 2 — Incomplete
- None. No half-built workflow_sms_attempts or history-log linkage columns (grep clean) — cleanly absent, not partial.

### Bucket 3 — Unnecessary / should be removed
- None. Three send_sms callers are distinct legit channels (executor sms_node_executor.py:87; admin API twilio.py:145; Retell task tasks/sms.py:49) sharing one core. Correct reuse.

### Bucket 4 — Missing / genuinely required
- None blocking. Two spec residuals defensibly optional.

### Report challenges
- workflow_sms_attempts / crash-safe claim NOT-REQUIRED — UPHELD. already_sent covers real dup causes; only sub-second 201→commit crash window uncovered (≤1 extra text, opt-out honored, no double-dial cost). Asymmetry vs voice (which HAS the claim) is coherent (double-dial cost).
- sms_history_logs linkage NOT-REQUIRED — UPHELD. Attribution lives on usage_events; nothing branches on SMS delivery. Would duplicate unused data.
- "100%" mild letter-overstatement but honest (residuals listed w/ reopen conditions).

### Test coverage
- test_outbound_sms_executor.py — renderer + executor incl. idempotent-already-sent (asserts send_sms+begin_step NOT called, still advances).
- test_inbound_sms_routing.py — correlation ambiguity boundaries.
- test_inbound_sms_intent.py — STOP/HELP/START, confirmation tokens, webhook sig fallback.
- Metering dedupe via test_usage_metering/test_usage_rollup.
- Gap (minor): no webhook-layer test asserting exactly-once metering across sent+delivered; SUPPRESSED-row path untested.

---

## Plan 05 — Outbound Email — VERDICT: Built slice complete & correct; ONE real Bucket-2 gap — Resend bounce/complaint webhook can't recover institution scope (tag shape mismatch + Resend doesn't echo tags) → suppresses nothing in prod as written

### Bucket 1 — Complete
- Transactional send path — email_node_executor.py:42-187 (resolve_email_from institution→platform, messaging_credentials.py:93-110, sandboxed renderer, clean fail paths). 11 executor tests pass.
- Send-time idempotency (XC-1) + crash-window (XC-1b) via stable Idempotency-Key email:{run}:{node} — email_node_executor.py:51-56,120-123.
- Usage metering (email=1), best-effort, idempotent — email_node_executor.py:166-185.
- Signed one-click unsubscribe token — email_unsubscribe.py:19-45 (HMAC keyed_hash binds institution:email_hash; raw address NEVER in URL, only hash_email — sms_privacy.py:122-132); footer on every body :101-113.
- Public GET /api/email/unsubscribe — email_compliance.py:38-50; reachable + suppressing.
- Resend webhook sig verification fail-closed in prod — email_compliance.py:53-67 (no secret+prod→403).
- Suppression writes REVOKED EMAIL ConsentRecord — tasks/email_compliance.py:35-65 → record_email_consent_identity (sms_compliance.py:489-520).
- Gate honors revoked (revoked beats implied) — compliance_gate_service.py:204-255 (_check_email_consent; REVOKED block at :246 BEFORE implied allow :243).
- DNC blocks email before consent check — compliance_gate_service.py:120-128.

### Bucket 2 — Incomplete
- **Resend webhook institution-scoping effectively non-functional in prod (REAL gap, not just "needs staging verify").** (1) Tag shape mismatch: executor sends tags as LIST of {name,value} (email_node_executor.py:130-132), webhook reads as DICT `(data.get("tags") or {}).get("institution_id")` (email_compliance.py:100) → .get() on a list raises AttributeError → unhandled 500 → Resend deactivates endpoint. (2) Resend does NOT echo custom tags on bounced/complained events → data.get("tags") is None → falls to data.get("institution_id") (absent) → institution_id=="" → every recipient hits "missing institution scope" skip (:106-112) → NOTHING suppressed. Tests only feed dict-shaped tags the system never emits → green against a nonexistent payload. Scope: unsubscribe (primary opt-out) works; only automated bounce/complaint suppression is broken. Fix small (map email→institution via recipient contact/consent, or read tags as list).

### Bucket 3 — Unnecessary / should be removed
- None. No half-built email_sending_profiles/workflow_email_attempts/workflow_email_templates (grep clean). 3 Resend send paths (campaign/auth/notification) are distinct, not duplicates.

### Bucket 4 — Missing / genuinely required
- Nothing beyond Bucket 2. E-3 HTML + E-4 per-tenant domain are agreed external QA-deferral (confirmed external, not flagged).

### Report challenges
- UPHELD: report's "NEEDS-STAGING-VERIFY" understates a real defect — downgrade to Bucket-2 incomplete (webhook suppresses nothing in prod as written).
- CONFIRMED: raw address never in URL; revoked beats implied; no migration (reuses email_hash); fail-closed prod webhook; from-address override+fallback.
- MINOR: report "12 tests" = compliance file only; actual 23 (12 compliance + 11 executor), all pass.

### Test coverage
- 23/23 pass. Covers token round-trip/tamper, unsubscribe valid/invalid, webhook 403/suppress paths, revoked-consent gate block, executor error/idempotent/from-address.
- GAP: no test sends webhook the executor's actual list-shaped tags → the Bucket-2 mismatch is untested.

---

## Plan 06 — Four Live Campaigns — VERDICT: SUBSTANTIATED; 4 templates wired to instantiation+enrollment; both previously-dead branches now genuinely reachable via real event→resume→branch; no sales-qual cruft

### Bucket 1 — Complete
- 4-template registry defined + wired — campaign_templates.py:145-184; consumed automation_templates.py:17-21,65,74,85-118; test asserts exactly 4.
- Confirmation `confirmed` branch REACHABLE (was dead): inbound SMS → _classify_confirmation_reply (twilio_webhooks.py:190,57,84-91) → resume_sms_confirmation.delay (:193) → _resume_waiting_runs_for_context_field(appointment_status=confirmed) (automation_workflow.py:1088,1184-1251) → condition check-confirmed → exit-confirmed.
- C-2 PMS write-back — _confirm_appointments_for_runs → adapter.confirm_appointment (automation_workflow.py:1102-1109,1297; nexhealth/adapter.py:499-527, capability-gated, fail-open, audited CONFIRM_APPOINTMENT).
- Reactivation `booked` branch REACHABLE (was dead): NexHealth appt webhook → resume_reactivation_booking.delay (nexhealth_webhooks.py:292-298) → _resume_waiting_runs_for_context_field(appointment_booked=True) (automation_workflow.py:1164,1171) → check-booked → exit-booked.
- Recall trigger idempotent paced enrollment — scan_recall_workflows/_enroll_recalls_for_institution (automation_workflow.py:1399,1452,1496; jitter 0.5-2.0s; make_recall_idempotency_key period YYYY-MM → at-most-once/patient/month).
- Appointment-offset trigger — trigger_appointment_workflows (:489,525,562,572).
- PmsLiveRevalidationService wired into resume (:1235) + dispatcher.

### Bucket 2 — Incomplete
- None for agreed scope.

### Bucket 3 — Unnecessary / should be removed
- None. Sales Qualification GENUINELY ABSENT (grep sales.qual/qualification = 0 hits). No dangling 5th template/orphan trigger. C-3 "dropped by design" verified.
- Note (not cruft): instantiate_template publishes v1 immediately → active not draft (automation_templates.py:89-97); documented intentional limitation.

### Bucket 4 — Missing / genuinely required
- None. All 4 campaigns reachable end-to-end.

### Report challenges
- "Dead branches now fixed / event-led" — TRUE, verified; distinct event sources converge on shared _resume_waiting_runs_for_context_field; _waiting_step_targets_field guard prevents false resumes.
- C-4/C-5 "not required" — HOLDS (workflow-layer versioning already exists via publish_version; template-library versioning is maintainability).
- Caveat (report omits): reactivation booked detection is webhook-only, no periodic reconciliation if webhook missed. Acceptable v1.

### Test coverage
- test_automation_campaign_templates.py (schema all 4, exact keys, trigger types, multi-exit, routes). test_inbound_sms_intent.py (confirm classifier). Adapter/webhook/resume referenced across task/webhook/adapter tests; recall scan in test_scheduled_jobs.py.
- Gap: no single integration test asserting end-to-end exit-booked/exit-confirmed OUTCOME after resume (reachability is by trace).

---

## Plan 07 — AI Callback Handling — VERDICT: Genuinely complete & reachable end-to-end; leaner opt-in design real (not half-built); all claims verified except a stale line citation

### Bucket 1 — Complete
- callback_requested trigger union member — definition_schema.py:55,59-70.
- CallbackTriggerService.find_active_callback_workflows — callback_trigger_service.py:23-41.
- compute_callback_eta / make_callback_idempotency_key — :44-66.
- trigger_callback_workflows task → _trigger_callback_async — automation_workflow.py:721-761,764-867.
- Retell webhook hook enqueues on NEEDS_CALLBACK after commit — webhooks.py:513-544.
- LOOP-GUARD REAL — webhooks.py:524 (`call_direction != OUTBOUND`) → AI callback's own webhook won't re-enqueue. No infinite loop.
- enroll_and_start_workflow_run delegation — automation_workflow.py:344-475 (inherits idempotency, gate, revalidation via dispatcher.advance :459-462).
- Express VOICE consent capture (XC-6/CB-3) — automation_workflow.py:810-832 (GRANTED/VOICE/EXPRESS, only if has_consent_record VOICE False :821 → respects prior REVOKED). NOTE actual 810-832 not report's 681-692.
- Gate closes loop — compliance_gate_service.py:136-139 (send_voice→_check_phone_consent VOICE) + DNC :122-128.
- Double-contact guard CB-2 — automation_workflow.py:799-805 (early-return if call.callback_resolved; field call.py:229-230, partial index :105).
- Preferred-time/quiet-hours via Celery eta (:858) + dispatch-time gate.

### Bucket 2 — Incomplete
- None blocking. Residual (self-disclosed automation_workflow.py:796-798): staff-resolve DURING the ETA delay not re-checked (CB-2 runs at trigger time, not dispatch). Narrow edge, only with future preferred time.

### Bucket 3 — Unnecessary / should be removed
- callback_automation_settings / callback_workflow_links — GENUINELY ABSENT (grep = 0 across src/alembic/tests). Nothing to remove. No dead 5th-template scaffolding.

### Bucket 4 — Missing / genuinely required
- None. Leaner opt-in works: opt-in == activating a callback_requested workflow (find_active returns [] when none → callbacks stay in manual queue). No settings table needed.

### Report challenges
- Loop-guard "522-538" — TRUE (guard at :524).
- Express VOICE consent "681-692" — STALE CITATION; actual 810-832; behavior correct.
- "tables replaced by leaner opt-in (not-required)" — TRUE/defensible, wired+tested.
- "11 unit tests" — actually 12, all pass.

### Test coverage
- test_outbound_ai_callback.py — 12 tests PASS (schema parse, ETA none/past/naive/future, idempotency key, find_active filter, schedules nothing/immediate, CB-2 skip, honors future time).
- Gaps: loop-guard skip not tested (read-verified); consent-not-overwritten + full enroll→voice-gate chain not callback-specific-tested (covered structurally elsewhere).

---

## Plan 08 — Campaign Management / Analytics UI — VERDICT: Substantially complete & REAL — every FE action wired to a verified backend route; usage cards read real Plan 11 rollups (no mocks); 2 genuine gaps: per-campaign secondary stat fallback + no component tests; U-2b DNC UI the one near-term gap

### Bucket 1 — Complete
- Campaign list → listCampaigns → GET /automation/workflows (automation-api.ts:11-14; route :351); pause/resume/archive real (:635,648,661).
- Institution outbound halt status/activate/release wired (automation-api.ts:103-118); route ordering /outbound-halt before /{workflow_id} VERIFIED (automation_workflows.py:430,465,531 vs 562); activate returns halted_runs, idempotent (:489-496,527).
- Per-campaign emergency halt + run cancel — emergencyHaltCampaign→POST /{id}/emergency-halt (:901); cancelCampaignRun→/runs/{run_id}/cancel (:790); dialogs + optimistic update.
- Manual enrollment UI — ManualEnrollDialog (CampaignDetail.tsx:144-246) searches real GET /institution/contacts, enrolls POST /{id}/enroll; payload matches EnrollRequest exactly (:60-66); gated, 409 on non-active; disables Enroll when not active.
- Runs table → GET /{id}/runs (:751); FE type matches WorkflowRunResponse field-for-field.
- Usage/cost cards REAL not mock — getUsageSummary/getUsageByCampaign → /institution/usage/summary + /by-campaign (usage_reporting.py:30,106,169); backend sums real UsageCostRollup (:141-142); FE types match Pydantic field-for-field.
- Role gating on all routes; app Dialogs replace native confirm().

### Bucket 2 — Incomplete
- Per-campaign secondary usage stats silently fall back to institution-wide totals — CampaignDetail.tsx:515,520,525 (`campaignUsage?.x ?? sms?.x`); if campaign rollup nulled/zero, card scoped to the campaign shows institution-wide figures. Misleading, not broken. Cost+events (primary cards) correctly scoped.
- getUsageByCampaign(undefined,200)+client-side .find (CampaignDetail.tsx:271-276) → 201st campaign silently shows $0. Unbounded-growth foot-gun.

### Bucket 3 — Unnecessary / should be removed
- None. No dead components, no stub/mock API calls, no placeholder data. workflow_run_updated SSE backend-only (not dead FE).

### Bucket 4 — Missing / genuinely required
- U-2b Privileged DNC admin UI (report's own P1): backend do-not-contact routes exist (Plan 12) but NO FE surface → staff can't record an off-channel opt-out. Promote toward near-term-required.
- Rest of deferred list holds for pilot scope: CSV (PHI surface), attributed revenue (no source), ops/replay+timelines (support tooling), SSE real-time (manual refresh works — UX nit), location scoping (multi-location only).

### Report challenges
- "100% essential operator scope" — SUPPORTED with 2 asterisks (secondary-stat fallback can mislead; U-2b DNC UI arguably essential yet deferred). Core loop genuinely built, not vaporware.
- usage cards consume Plan 11 — TRUE (real rollup reads, exact schema match).
- Manual enrollment + /outbound-halt reorder — TRUE and load-bearing.

### Test coverage
- src/test/automation-api.test.ts — 7 API-client tests (enroll payload, cancel, usage summary/by-campaign, halt get/activate/release, emergency halt).
- GAP: no component/render tests for Campaigns.tsx or CampaignDetail.tsx (contrast Plan 02). Dialogs, disabled-state, stat-fallback, isCancelable untested at component level. Backend routes covered by test_automation_workflow_routes.py + integration.

---

## Plan 09 — Integration & Data Layer — VERDICT: BUILT CODE COMPLETE, WIRED, INTERNALLY CORRECT; Finding E genuinely closed; ~80% honest (remaining 20% = deferred D-5/D-6 staging); no dead code

### Bucket 1 — Complete
- D-1 Reschedule re-enroll WIRED (closes Finding E): webhook classifies rescheduled when start_time differs (nexhealth_projection_service.py:209) → cancels old runs+timers (_cancel_runs_for_appointment nexhealth_webhooks.py:270-273,37-78) → re-fires trigger with time-aware idempotency key appt:{ver}:{appt}:{utc} (appointment_trigger_service.py:87-104) so new-time enrollment not deduped.
- D-2 Freshness window: _check_projection consults appointment_working_set first, trusts row synced <900s, else live get_appointment (revalidation.py:75,117-196); wired into dispatcher (step_dispatcher.py:146; injected automation_workflow.py:242,456).
- D-3 Disposable projection AppointmentWorkingSet + migration 20260707 (RLS incl nexhealth_webhooks+celery).
- D-4 Event ledger NexHealthWebhookEvent + claim_event self-healing reclaim of PROCESSING >300s (nexhealth_projection_service.py:73-126); race-safe begin_nested; claimed at receipt (nexhealth_webhooks.py:214); perf index folded into 20260707 migration.
- Cancellation path terminates runs+timers (nexhealth_webhooks.py:25-34,243-256).
- Subscription/backfill/reconciliation internally coherent (mock-tested); beat wired worker.py:65-75.

### Bucket 2 — Incomplete
- None in built scope. D-5/D-6 known-deferred to QA (mocked-only), not counted.

### Bucket 3 — Unnecessary / should be removed
- None. recall_eligibility_working_set has ZERO refs (correctly NOT built = D-6 deferred decision, not stub). Cosmetic nit: identity remap automation_workflow.py:687 (harmless).

### Bucket 4 — Missing / genuinely required
- None within code scope. webhook-events table correctly folded into 20260707 migration (not its own file).

### Report challenges
- Finding E closed / reschedule re-enrolls — UPHELD (time-aware key present+tested).
- Self-healing reclaim, freshness window — UPHELD.
- ~80% honest — no code gap; D-5/D-6 genuinely validation/decision.
- Minor: D-4 ledger lives inside 20260707 merge migration, not own file.

### Test coverage
- 33 Plan-09 unit tests pass (APP_ENV=test venv pytest). Covers upsert new/unchanged/rescheduled/cancelled; claim fresh-blocks/stale-reclaimable/failed-reclaimable; time-aware key; fresh-projection-skips-live / stale-falls-through; fail-open unwired.
- Gap (= D-5): no live staging integration; no real-Postgres integration for projection/ledger (unit-only) — weakest seam.

---

## Plan 10 — Per-Tenant Provisioning — VERDICT: Complete for agreed scope; encryption real AES-256-GCM, token never returned; one stale doc comment (Bucket 3); no functional gaps for pilot

### Bucket 1 — Complete
- AES-256-GCM real (AESGCM, random 96-bit IV, 32-byte key, typed DecryptionError, never logs plaintext) — institution.py:55-154.
- 4 encrypted columns + decrypting properties — institution.py:220-281; migration 20260703 (idempotent ADD COLUMN IF NOT EXISTS).
- TenantTwilioCredentialResolver institution→platform fallback — messaging_credentials.py:50-155.
- Consumed by all 3 callers, no bypass: SMS (sms_service.py:201-202), email (email_node_executor.py:80), webhook sig (twilio_webhooks.py:314-320).
- Super-admin API: no token field, masked SID only, SUPER_ADMIN gated — admin_institutions.py:1564-1689.
- Audit on PATCH+DELETE (INSTITUTION_UPDATE, masked) — :1636-1649,1676-1688.
- ChannelReadinessService + GET /channel-readiness warning-only (is_publishable blocks only on error) — channel_readiness.py:89-123; validation_service.py:147-148.

### Bucket 2 — Incomplete
- None. No caller bypasses resolver.

### Bucket 3 — Unnecessary / should be removed
- STALE COMMENT validation_service.py:13-14 says readiness "blocks publishing" — code is warning-only. Doc-cleanup, zero functional impact (report's NIT confirmed).
- Cosmetic: migration header says 20260703_institution_provisioning but revision="20260703_provisioning". Harmless.

### Bucket 4 — Missing / genuinely required
- None. Large "not-required" list (vendor automation, A2P/10DLC, onboarding lifecycle, feature flags) genuinely not-required for pilot AND no half-built cruft (verified no partial onboarding table/A2P model/flag columns). External caveat: per-tenant email deliverability = DNS/vendor (Plan 05 overlap), no code owed.

### Report challenges
- AES-256-GCM real / token never returned / consumed by 3 callers / warning-only / audit — all TRUE, exact lines.
- validation_service.py:13-14 NIT — confirmed stale comment (doc), not code contradiction.
- 100% for agreed scope accurate (rests on CTO scope decision, legitimate).

### Test coverage
- Strong: test_institution_encryption.py (3), test_institution_provisioning.py (9), test_messaging_credentials.py (11 fallback matrix), test_channel_readiness.py (12), RBAC matrix.
- Gap (minor): no test asserts audit rows written; no negative test proving response never contains token (enforced structurally by Pydantic).

---

## Plan 11 — Usage & Cost Reporting — VERDICT: SHIPPED & COMPLETE for scope; all 7 milestones wired; TWO real Bucket-2 attribution gaps (SMS entirely + voice workflow_id NULL) → /by-campaign under-reports; no usage_budgets cruft

### Bucket 1 — Complete
- UsageEvent + partial UNIQUE idempotency index (institution_id,idempotency_key) — usage_event.py:78-84; migration 20260704 + RLS.
- Idempotent metering: add→begin_nested→flush; IntegrityError→backfill — usage_metering_service.py:102-122.
- SMS late-price backfill REAL: sms:{sid} key shared by sent(null price)+delivered(price); _backfill_costs fills NULL in place — twilio_webhooks.py:280-291; usage_metering_service.py:124-165.
- All-channel ingestion, counts exact under Option B: SMS segments+Price; email emails=1; voice minutes+dials via metadata.workflow_run_id (retell/webhooks.py:~428). $0 cost = pricing decision, counts exact.
- Campaign attribution cols + partial index; migration 20260706_usage_event_campaign_tags.
- Rollup table + UsageRollupService UPSERT-from-SELECT + delete-empty; migration 20260706_usage_cost_rollups + RLS.
- Reporting API /summary (rollup) + /by-campaign (raw, workflow_id-grouped) — main.py:262.
- Group endpoint GET /api/group/usage-summary GROUP_ADMIN + migration 20260710_usage_group_rls.
- Scheduled recompute WIRED (real FargateTaskDefinition + EventBridge Rule → EcsTask) — stack.py:735-752,1066,1129-1141.

### Bucket 2 — Incomplete
- SMS campaign attribution: ingestion leaves workflow_run_id/workflow_id NULL (twilio_webhooks.py:280-291) → /by-campaign OMITS SMS entirely. Known (model comment usage_event.py:105-110), pending Plan 04 sms_history→workflow linkage.
- Voice workflow_id NULL: voice sets workflow_run_id but NOT workflow_id → /by-campaign (groups by workflow_id) EXCLUDES voice. Asymmetric vs email (sets both).

### Bucket 3 — Unnecessary / should be removed
- None. No usage_budgets model/migration anywhere (grep = 0) — "half-built budgets" DISPROVEN, never in scope. No rollup column computed-but-unread. Stale TODO(Plan 03) comment usage_metering_service.py:44-48 (harmless).

### Bucket 4 — Missing / genuinely required
- Nothing blocking. The two attribution gaps are Bucket-2 partial-coverage, not missing pillars.

### Report challenges
- Report claims voice sets workflow_run_id + workflow_id via metadata — PARTIALLY WRONG: voice sets only workflow_run_id; workflow_id NULL → voice invisible in /by-campaign. Report OVERSTATES voice campaign attribution.
- Report implies SMS carries campaign tags — FALSE at ingestion (NULL); known deferred linkage.
- Doc drift: recompute_usage_rollup.py:3 says "5-minute" but stack.py:751 wires 15min (intentional). Cosmetic.
- All other anchors verified accurate.

### Test coverage
- Strong: test_usage_metering.py (6), test_usage_late_price.py (4 incl. does-not-overwrite), test_usage_rollup.py (6 incl. every metric column + campaign-tag persist), test_usage_reporting.py (5 window).
- Gaps: no test asserts voice workflow_id NULL vs email both-set; no integration on group /usage-summary RLS branch.

---

## Plan 12 — Compliance & Consent — VERDICT: Genuinely complete for scope; gate structurally unbypassable; basis HARD block; implied-transactional correctly after opt-out; staff DNC route real/RBAC/audited; no Bucket-3 dead code

### Bucket 1 — Complete
- Gate before EVERY send — structurally unbypassable: all 3 send types share one branch step_dispatcher.py:142; gate.check at :164 BEFORE executor constructed :202-208; executors only reachable via get_action_executor inside gated branch.
- Single gated build_dispatcher (real gate injected :420-421); all 5 call sites route through it; NoOpComplianceGate only class default for unit tests.
- Gate ordering CORRECT: halt→quiet-hours→contact-exists→DNC(voice/email)→per-channel consent (compliance_gate_service.py:77-139).
- Consent-basis HARD BLOCK: marketing→express_written, recall→express, care/unset→any; insufficient→block *_consent_basis_insufficient; revoked→block; NULL/legacy→implied→marketing blocked (:233-255); ConsentBasis enum+basis col (sms_consent.py:33-40,88-90); migration 20260707_consent_basis. Warn-at-publish + hard-block-at-gate.
- Implied transactional AFTER opt-out — SAFE: only when record is None AND content_class∈{None,transactional_care}; DNC+REVOKED checked earlier → opted-out never reached via implied; SMS never reaches implied (assert_can_send).
- Staff DNC route real/RBAC/audited: POST/DELETE/GET /institution/do-not-contact, get_current_institution_admin, DO_NOT_CONTACT_CREATE/RELEASE (do_not_contact.py:72-150); group-scope creation rejected via Literal.
- DNC scope tiers honored (INSTITUTION+GROUP tenant-wide). DST-correct quiet hours (ZoneInfo/astimezone). Email-identity consent (Finding D). AI-voice disclosure. Bilingual FR STOP inbound. ContentComplianceValidator wired publish+/validate. Emergency halt checked first.

### Bucket 2 — Incomplete
- Group-scope DNC CREATION not exposed (gate honors GROUP, route caps at location/institution) — DSO group-wide opt-out needs per-institution entries. Deferred to GROUP_ADMIN.
- Commercial express-consent CAPTURE not built (deferred w/ lead-intake); gate correctly BLOCKS meanwhile (fail-safe). Express VOICE consent on callback path exists.
- Outbound STOP footer English-only (sms_privacy.py:15) — report's "bilingual EN/FR STOP" holds for INBOUND recognition (compliance-critical) but not outbound footer.

### Bucket 3 — Unnecessary / should be removed
- None. NoOpComplianceGate legit test double. No config-gated compliance that can't activate. Named ConsentService/SuppressionService simply don't exist (logic in SmsComplianceService+ComplianceGateService) — optional refactor, not gap. Every basis path reachable+tested.

### Bucket 4 — Missing / genuinely required
- None blocking for pilot. Two deferrals fail-safe (gate blocks). Challenge: US cross-timezone quiet hours is a real but narrow TCPA edge (patient TZ≠clinic TZ) — revisit before high-volume; correctly not-required for pilot. Group-scope DNC creation = real minor gap, correctly GROUP_ADMIN follow-up.

### Report challenges
- Gate-before-every-send, single path, basis hard-block, implied-after-optout, DNC RBAC+audit, DST, email-identity, disclosure, inbound FR STOP, validator wiring, migrations — all TRUE.
- Overstatement: "bilingual EN/FR STOP" holds inbound; outbound footer English-only.
- 100% for scope defensible; two remainders fail-safe deferrals.

### Test coverage
- 40 pass (25 gate + DNC route + content validator). Covers halt, quiet-hours allow/hold/block, DNC per-channel isolation, implied email+voice, commercial blocked, marketing basis-insufficient NULL basis.
- Gap: all unit/mock — no real-Postgres integration asserting a blocked send end-to-end for a basis violation.
