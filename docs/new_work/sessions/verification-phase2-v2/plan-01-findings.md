# Plan 01 — Workflow Engine — Verification Findings

Audited: 2026-07-03. Evidence-based against actual code, not session docs.

## Scope of Plan 01
Durable, multi-tenant, timezone-aware workflow runtime: definition/version tables,
runs, step executions, durable timers, scheduler with distributed claiming, runtime
state machine, idempotent enrollment, draft/publish/version lifecycle, compliance-gate
seam, action/trigger registries, quiet-hours, emergency halt.

## Data Model — COMPLETE (matches plan closely)
`src/app/models/automation_workflow.py`:
- `AutomationWorkflow` L67 — status draft/active/paused/archived (CheckConstraint L72), current_version_id self-alter FK L99, `definition`/`trigger_type` derived props L144-157.
- `AutomationWorkflowVersion` L160 — immutable snapshot; version_number unique per workflow L165; `definition` JSONB L198; `definition_checksum` L204; `content_classification` L205; published_by/at.
- `AutomationWorkflowRun` L226 — workflow_version_id FK RESTRICT L264 (version pinning); idempotency_key L276; trigger_ref_type/id/metadata; status incl. blocked; current_step_id.
- `AutomationWorkflowStepExecution` L302 — attempt_number/max_attempts L358-359; scheduled_at + scheduled_local_at + scheduled_timezone (DST diagnostics) L360-365; UniqueConstraint (run,step,attempt) L307.
- `AutomationWorkflowTimer` L380 — due_at/due_local_at/timezone; claim_token/claimed_at/claim_expires_at; status pending/claimed/fired/cancelled.
- `AutomationWorkflowEvent` L444 — append event stream, event_metadata JSONB, no raw PHI.
- Plan-named `workflow_enrollment_locks` table NOT created — replaced by idempotency unique index (acceptable substitute).

## Migration — COMPLETE
`alembic/versions/20260702_auto_workflow_core.py`:
- All 6 tables (L73-247), deferred self-FK via DO-block L114-132.
- RLS enabled+forced per table with celery/dead_letter/user context policy `_rls_expr` L32-53, `_enable_rls` L56-67.
- Indexes by institution/location/status/due (L249-289).
- Idempotency unique index `uq_automation_workflow_run_idempotency` on (institution_id, workflow_version_id, idempotency_key) WHERE not null — L266.
- `uq_automation_timer_active_step` partial unique on step_execution_id WHERE status in pending/claimed — L279 (one active timer per waiting step).
- Grants to nexhealth_app L294-311.
- NOTE: app-level enrollment dedup query filters only (institution_id, idempotency_key) (enrollment_service.py L51-57) but DB unique index also keys on workflow_version_id. App check is stricter/broader; safe in practice but the two dedup scopes differ.

## Definition Schema — COMPLETE (Pydantic, not JSON-Schema)
`src/app/services/automation/definition_schema.py`:
- Triggers: appointment_offset, recall_scan, manual, bulk_import (discriminated L46).
- Nodes: wait, send_sms, send_voice, send_email, condition, exit (discriminated L171).
- Wait delays: duration + calendar(offset_days,time_of_day HH:MM) L61-79.
- Graph validation model_validator L191: entry node exists, next_node refs valid, condition branch refs valid, at least one exit. extra="forbid" → immutability.
- Gaps vs plan: no quiet-hours *policy reference* block, no explicit merge-field/compliance-metadata block in schema (respect_quiet_hours bool flag per node only). No unreachable-node detection (only ref-existence).

## Lifecycle / Definition Service — COMPLETE
`src/app/services/automation/definition_service.py`:
- create_draft L38, publish_version L116 (validates via WorkflowDefinition, computes next version, sha256 checksum L161, flips to active, pins current_version).
- pause/resume/archive L185-213. update_draft only allowed in draft L104.
- Immutability enforced by publishing new versions; publishable statuses = draft|paused L26.

## Enrollment — COMPLETE (basic) / PARTIAL (eligibility gates)
`src/app/services/automation/enrollment_service.py`:
- Idempotent enroll L30 returns (run, created); dedup by institution+key L50-59; emits run.enrolled event.
- cancel_run L93.
- MISSING per plan: no frequency cap (≤1/day,≤3/wk), no enrollment-ceiling/spend caps, no conflicting-active-run dedup beyond idempotency key. (These are Plan 12 deps; not implemented here.)

## Runtime state machine — COMPLETE
`src/app/services/automation/runtime_service.py`:
- start_run/begin_step/complete_step/fail_step/wait_run/resume_run/complete_run/fail_run with guarded transitions and event emission `_emit` L166. Terminal-status guard L20.

## Scheduler (durable timers) — COMPLETE
`src/app/services/automation/scheduler_service.py`:
- create_timer L33; claim_due_timers L58 uses `.with_for_update(skip_locked=True)` L83 + claim_token + TTL (exactly-one dispatch, distributed-safe).
- fire_timer L98; cancel_timers_for_run L104; recover_stale_claims L124 (resets expired claims → pending).

## Step dispatcher / runtime interpreter — COMPLETE (send nodes now live)
`src/app/services/automation/step_dispatcher.py`:
- advance() L68 loop over nodes; WaitNode creates step+timer+waits L95; Send nodes gated then executed (SMS live via SmsNodeExecutor L132, Email via EmailNodeExecutor L136, Voice still `_dispatch_send_stub` L140/228); Condition eval L142; Exit L152. _MAX_STEPS=50 guard.
- resume_after_timer L166 — validates waiting state, finds waiting step, resumes, advances past wait.
- `_compute_due_at` L265 uses zoneinfo (DST-safe), duration vs calendar; unknown-tz → UTC fallback L275.
- Condition ops eq/neq/in/not_in/is_null/is_not_null L248.

## Celery tasks — COMPLETE
`src/app/tasks/automation_workflow.py`:
- poll_workflow_timers L60 (beat, queue "workflow") claims+enqueues per-timer dispatch; dispatch_workflow_timer L113 loads timer/run/version, pins version (session.get version L167), resolves location tz, uses REAL `ComplianceGateService` L184, resume_after_timer, exp backoff `_retry_countdown` L210.
- enroll_and_start_workflow_run L225 (also uses ComplianceGateService L326).
- trigger_appointment_workflows L358 + scan_recall_workflows L474 (recall enrollment is a STUB L491-527 — needs NexHealth patient history, Plan 09).
- Beat schedule wired in `src/app/worker.py` (poll_workflow_timers + scan_recall_workflows).

## Compliance gate — SEAM COMPLETE; real impl present (Plan 12)
- `compliance_gate.py`: Protocol + GateResult(allow/block/hold) + NoOpComplianceGate.
- `compliance_gate_service.py` L34: real gate — emergency halt (block) L49, quiet hours (hold) L57, SMS via SmsComplianceService L138, email/voice via ConsentRecord L156. Quiet-hours logic inline here (no standalone QuietHoursService).

## API routes — COMPLETE
`src/app/api/routes/automation_workflows.py`: CRUD/validate/versions/lifecycle/enroll/runs/cancel/bulk-enroll + outbound-halt GET/POST/DELETE. merge-fields endpoint L267.

## BUGS / GAPS
1. **Inline enroll route bypasses the real compliance gate.** `enroll_in_workflow` L463 builds `WorkflowStepDispatcher(session, runtime, scheduler)` with NO gate arg → defaults to NoOpComplianceGate. So a synchronous enrollment whose first step is a send node (manual/immediate workflows) sends with NO halt/quiet-hours/consent check. The Celery paths correctly pass ComplianceGateService. Inconsistent and a real compliance hole.
2. **Inline enroll route hardcodes `location_timezone="UTC"`** L469 — calendar waits/quiet-hours for the first inline advance ignore the location timezone. Celery paths resolve it correctly.
3. **Emergency halt is a soft send-time block, not a mid-flight terminate.** Plan §Services requires "emergency halt … terminate all in-flight runs, cancel pending timers & queued attempts" (Finding 9). Actual: OutboundEmergencyHalt only causes the gate to return block at the next send dispatch; waiting runs/timers are NOT cancelled and continue to fire. No `WorkflowDefinitionService.emergency_halt` / version-scoped halt exists (halt is institution-wide only).
4. **Paused workflow does not stop in-flight waiting runs from advancing.** dispatch_workflow_timer never checks workflow.status; a waiting run on a paused workflow still advances when its timer fires. Plan lists "run is waiting when workflow is paused" as an edge case; only *new* enrollment is gated (route L430). Arguably matches "pause stops new enrollments only" but the waiting-run edge case is unhandled.
5. **Dispatch-time re-validation of trigger data is limited.** Consent/halt/quiet-hours are re-checked at send via the gate (good), but appointment-cancelled / appointment-state-changed rechecks (plan Edge Cases, Technical Considerations "recheck gates at dispatch") are NOT implemented — context is just `run.trigger_metadata` captured at enrollment (tasks L191).

## ARCHITECTURAL CONCERNS
- Plan-named modular seams **not** built as separate components: `WorkflowActionRegistry` (action dispatch is if/isinstance in step_dispatcher L117-158), `WorkflowTriggerRegistry` (triggers are ad-hoc Celery tasks), `WorkflowValidationService` (validation is inline Pydantic + publish_version), `QuietHoursService` (inline in ComplianceGateService). Functionality exists but not the extensible registries the plan called for — adding channels/triggers requires editing core dispatcher/tasks.
- No blast-radius / spend-cap / content-compliance validator at publish (Plan 12 deps, correctly deferred, but publish is therefore not "fail-closed on compliance" as plan §Architecture Decisions states).
- Scheduler paced dispatch (NexHealth ~1000/min budget, Retell concurrency, Twilio limits, jitter/smoothing) from Technical Considerations is NOT implemented — timers fire in claim batches of 50 with no rate pacing/jitter.

## TECHNICAL DEBT
- Two enrollment code paths (inline route vs Celery task) with divergent gate/timezone wiring (bug 1/2) — should converge on one enroll+advance helper.
- recall scanner is a stub returning counts only.
- Voice send node still a stub (`_dispatch_send_stub`).
- App-vs-DB idempotency scope mismatch (version-agnostic app query vs version-keyed unique index).

## CODE QUALITY
- Strong: clear docstrings, typed, RLS-aware system sessions, FOR UPDATE SKIP LOCKED, checksum on versions, event emission, exp backoff, extra="forbid" schemas, PHI-conscious event model.
- Response models built while session open (avoids async lazy-load issues) — matches findings.md note.

## TESTS
Present: test_automation_workflow_models, _migration_static, _definition_schema, _definition_service, _enrollment_service, _runtime_service, _scheduler_service, _step_dispatcher, _workflow_routes, _workflow_task, _campaign_templates, _compliance_gate, _compliance_gate_service, _plan09.
Ran (unit, sqlite): scheduler + definition_schema + step_dispatcher = **52 passed**.
Cover: timer claim/stamp/stale-recovery, schema graph validation, dispatch advance/wait/condition/exit, DST/timezone due-at. 
Gaps in coverage: no test for inline-enroll-route gate bypass (bug 1), no worker-crash-after-claim integration, no publish-immutability/version-pinning integration test observed at unit level (RLS static test only checks migration text).

## VERDICT
The durable non-sending engine core (models, migration+RLS, versions, runs, timers with distributed claiming, state machine, scheduler, dispatcher, Celery polling, compliance-gate seam + real Plan-12 gate) is genuinely built and tested — strong alignment with the plan's foundational scope. Deviations: emergency-halt is a soft block not a mid-flight terminate, the inline enroll route bypasses the real compliance gate and hardcodes UTC, and the modular action/trigger/validation/quiet-hours registries plus paced/rate-limited dispatch were collapsed into inline logic or deferred.
