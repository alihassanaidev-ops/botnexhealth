# Plan 04 — Outbound SMS — Verification Findings

Audited: 2026-07-03. Evidence-based against actual code (not session docs).

## Scope of the plan vs. what the session actually attempted

The plan (`docs/new_work/Implementation Plans/04-outbound-sms.md`) is broad:
SMS workflow action executor, per-tenant Twilio send, delivery webhooks, STOP/opt-out,
suppression + consent gate before send, quiet-hours hold, idempotent dispatch with a
`workflow_sms_attempts` table, `inbound_sms_messages` + `InboundSmsRoutingService` for
free-text replies, campaign linkage fields on `sms_history_logs`, and segment/price metering.

The session (`docs/new_work/sessions/outbound-04-sms/`) explicitly narrowed the goal to:
"Replace `_dispatch_send_stub` for `SendSmsNode` with a real SMS send using `SmsService`."
It shipped 4 slices: template renderer, `SmsNodeExecutor`, dispatcher wiring, tests.
Much of the plan's data model + inbound/metering surface was left unbuilt or delegated to
Plans 01/10/12.

## Verified — Completed

- **SMS action executor** — `src/app/services/automation/sms_node_executor.py:20-95`.
  Resolves contact (`run.contact_id`), phone, location + `twilio_from_number`, renders body,
  calls `SmsService.send_sms(...)`. Fail-safe on every error path (fail step + fail run).
- **Template renderer** — `src/app/services/automation/template_renderer.py:94-119`. `{{var}}`
  regex substitution. Static allowlist `STATIC_MERGE_FIELDS` (patient name fields, clinic_name)
  at L54-91, shared with the merge-field catalog API. Unknown vars → blank (never exposes raw
  placeholder). Trigger metadata / campaign context keys pass through (L112-114).
- **Dispatcher wiring** — `step_dispatcher.py:117-140`. `SendSmsNode` → `SmsNodeExecutor`;
  `SendEmailNode` → `EmailNodeExecutor`; `SendVoiceNode` still on `_dispatch_send_stub` (L140,228).
- **Compliance gate enforced BEFORE send (in dispatcher)** — `step_dispatcher.py:118-130`.
  `gate.check(run, node.type)` → block/hold/allow. Implemented by Plan 12
  `ComplianceGateService` (`compliance_gate_service.py:40-84`): emergency halt (block),
  quiet hours (hold), SMS consent+suppression via `SmsComplianceService.assert_can_send`
  (`_check_sms` L138-154), email/voice via `ConsentRecord`.
- **Per-tenant Twilio credential resolution** — `sms_service.py:199-203`: resolves
  `institution.twilio_account_sid` / `twilio_auth_token` (from the location's institution),
  falls back to platform creds (`_get_twilio_client` L45-58). Plan 10 dependency satisfied.
- **Defense-in-depth compliance in SmsService** — `sms_service.py:126-170`: `assert_can_send`
  again at send time; on `SmsSendBlockedError` writes a `SUPPRESSED` `SmsHistoryLog` row (no send).
- **Sender enforcement** — `sms_service.py:96-104`: requested from-number must match the
  location's configured `twilio_from_number` or `ValueError`.
- **Delivery status webhook** — `twilio_webhooks.py:134-166` (`POST /sms-status`): verifies form,
  `SmsService.update_delivery_status(message_sid, provider_status, provider_error)`; unmatched SID
  → `capture_dead_letter`. Updates `sms_history_logs` only.
- **Inbound STOP/START/HELP** — `twilio_webhooks.py:29-133`. Whole-token keyword classify
  (`_classify_intent` L38-52; STOP wins over START). STOP → `compliance.suppress`; START →
  `release_suppression`; HELP → help text; all audited.
- **Tests** — `tests/unit/test_outbound_sms_executor.py` (13: 7 renderer + 6 executor),
  `test_automation_compliance_gate_service.py`, `test_inbound_sms_intent.py`,
  `test_sms_block_reason_logging.py`, `test_sms_compliance_privacy.py`,
  `test_automation_step_dispatcher.py`. Ran `test_outbound_sms_executor` +
  `test_automation_compliance_gate_service`: **26 passed** (JWT_SECRET/APP_ENV env required).

## Missing (vs plan)

- **`workflow_sms_attempts` table + idempotency** — NOT built. No file in `src/app/models/`
  matching sms_attempt/inbound_sms/workflow_sms. No unique key on
  `(workflow_run_id, workflow_step_id, attempt_number)`. `SmsNodeExecutor` has NO dedupe —
  re-advance / timer redelivery can send the same node twice. Plan required idempotent dispatch.
- **`sms_history_logs` campaign/workflow linkage fields** — NOT added. `sms_history_log.py`
  has no `workflow_run_id`, `workflow_step_id`, `campaign_id`, `attempt_number`, `template_id`,
  `provider_segments`, `price_amount/currency`. SMS sends are not traceable back to the run/step.
- **Segment / price metering** — NOT captured. No `NumSegments`/`provider_segments`/`price`
  handling in `sms_service.py` (`update_delivery_status` L279). No usage-metering hook (Plan 11).
- **`inbound_sms_messages` table + `InboundSmsRoutingService`** — NOT built. Free-text replies
  are IGNORED: `twilio_webhooks.py:123-133` logs "Inbound SMS ignored" and returns empty TwiML.
  No persistence, no staff notification, no workflow-run correlation. Plan v1 required staff
  notification for free-text.
- **Delivery callback → workflow attempt notification** (plan step 5) — status webhook updates
  the log only; no workflow linkage to notify (no attempts table to link to).
- **Template PHI allowlist enforcement** — renderer does NOT reject unapproved PHI. Any key in
  `run.trigger_metadata` is injectable via `{{key}}` (`template_renderer.py:112-114`); no
  content-class validation as the plan's `SmsTemplateRenderer` required.
- **CSV/bulk enrollment consent provenance before SMS** (plan step 8) — not present in executor.

## Bugs / implementation gaps

1. **API inline dispatch bypasses the compliance gate** —
   `automation_workflows.py:463` builds `WorkflowStepDispatcher(session, runtime, scheduler)`
   with NO `gate` → defaults to `NoOpComplianceGate` (`step_dispatcher.py:66`). The Celery paths
   pass `gate=ComplianceGateService(session)` (`tasks/automation_workflow.py:184,326`). So a run
   enrolled+advanced inline whose first node is a `SendSmsNode` (no leading wait) is sent WITHOUT
   emergency-halt and quiet-hours enforcement. Consent/suppression are still caught by
   `SmsService.assert_can_send` (defense-in-depth), but **emergency HALT and quiet-hours HOLD are
   silently skipped** on this path. Inconsistent + a compliance risk.
2. **No idempotency guard** — see missing table above; duplicate-send exposure on retry/redelivery.
3. **Quiet-hours "hold" drops the run instead of deferring** — `step_dispatcher.py:124-130` treats
   `action=="hold"` as `complete_run(outcome="compliance_hold")`, i.e. the message is dropped, not
   rescheduled to the next open window. `compliance_gate_service.py:8-9` docstring acknowledges
   "Re-queue when conditions improve is deferred." Scope wants quiet-hours to hold-and-resume.
4. **`location_timezone` hardcoded "UTC" on inline advance** — `automation_workflows.py:469`
   passes `location_timezone="UTC"` so wait/calendar-delay scheduling ignores the location tz on
   the inline path (the gate re-resolves tz itself, so quiet-hours calc is unaffected, but timers are).

## Architectural concerns

- Compliance is enforced in the dispatcher, not in a dedicated `WorkflowSmsActionService` as the
  plan specified. Acceptable (one gate call shared across send channels) but diverges from plan.
- Hold==terminate means nighttime sends are silently dropped rather than delayed — a real
  behavioral gap for "quiet hours" as a scheduling feature vs. a kill.
- No separation of attempt state from the immutable delivery log (`SmsHistoryLog`), which the plan
  explicitly wanted (`workflow_sms_attempts` referencing the log).

## Technical debt

- Two dispatch entry points (inline API vs Celery) with different gate wiring — the inline path
  is the divergent one and should also inject `ComplianceGateService`.
- Renderer context passthrough with no allowlist for dynamic keys is a latent PHI-leak surface.
- Retry/dead-letter for SMS send failures deferred (executor fails the whole run on Twilio error —
  session decision D3).

## Code quality observations

- `SmsNodeExecutor` is clean, well-commented, fail-safe on every branch; result codes are precise
  (`no_contact`, `contact_not_found`, `no_phone`, `no_from_number`, `send_failed`, `sent`).
- Renderer's single-source-of-truth `STATIC_MERGE_FIELDS` (shared with catalog API) is a good pattern.
- Gate service is readable and priority-ordered with structured `GateResult` reasons.
- PHI hygiene is strong: hashed/masked phones, encrypted body, structured block reasons (not
  stringified exceptions), retention windows resolved per-institution.

## Tests

- Exist and pass (26 verified). Cover: renderer known/unknown/context vars; executor no-contact /
  no-phone / no-from-number / send-success / Twilio-error; gate service block/hold/allow paths;
  inbound intent classification; block-reason logging; SMS privacy.
- NOT covered (because unbuilt): idempotency, attempt table, inbound free-text routing, segment
  metering, and the inline-API-path gate bypass (bug #1 has no test catching it).

## Scope alignment verdict

The session delivered exactly its self-scoped goal (stub → real SMS send with compliance gate and
per-tenant creds) and it is solid and tested. But measured against **Plan 04's** and the Scope
doc's SMS deliverables (idempotent dispatch, delivery→workflow linkage, opt-out feedback into runs,
free-text inbound routing, segment metering, quiet-hours *hold-and-resume*, campaign linkage), it
is a partial implementation. Core compliant send path works on the Celery execution path; the
inline API path has a real compliance-gate bypass (bug #1).
