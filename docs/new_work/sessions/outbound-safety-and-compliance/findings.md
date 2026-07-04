# Findings — Outbound Safety & Compliance

Grounded current-state (two parallel research passes, 2026-07-04). Migration head:
**`20260703_retell_from_number`** (`down_revision = 20260704_usage_events`). Chain a new
migration off this head; use idempotent raw SQL (`IF NOT EXISTS` / `DROP … IF EXISTS`) +
RLS policy + `nexhealth_app` grant blocks (pattern: `20260703_outbound_halt.py`).

## P0-1 — NexHealth webhook fails open ✅ FIXED
- `nexhealth_webhooks.py:81-89` `_verify_signature` returned early when secret empty.
- `config.py`: default `nexhealth_webhook_secret=""` (L76); `is_production` property (L337);
  established prod-guard pattern (ENCRYPTION_KEY L207 etc.) raises `ValueError` in the settings validator.
- **Fix applied:** added prod guard in `config.py` (raise if `is_production and not nexhealth_webhook_secret`);
  defense-in-depth 403 in `_verify_signature` when prod + empty. Verified: local OK, prod-without-secret raises.

## P0-2 — Email consent keyed on phone hash (structural)
- `compliance_gate_service.py:88-90`: email+voice both routed through `_check_explicit_consent` (L128-156),
  which derives identity from `contact.phone` → `hash_phone` (`sms_privacy.py`), blocks `no_phone` (L135-139)
  if no phone. Email-only contacts can never pass.
- `ConsentRecord` (`models/sms_consent.py:33-68`): keyed on `phone_hash` (String(64), **NOT NULL**), index
  `(institution_id, channel, phone_hash)`. **No email column.** `ConsentChannel` = sms/email/voice (all present).
  CHECK already allows email/voice (migration `20260703_consent_channel`).
- `Contact` (`models/contact.py`): has `email` property (decrypt of `email_encrypted`); `phone_hash` col exists;
  **no `email_hash`.** True identity = `(institution_id, nexhealth_patient_id)`.
- SMS working path to mirror: `sms_compliance.py:102-114` (query by `(institution, channel=sms, phone_hash)`).
- **Fix plan:** add `hash_email()` to `sms_privacy.py`; add nullable `email_hash` to `ConsentRecord` +
  index `(institution_id, channel, email_hash)`; make `phone_hash` nullable (email-only rows). Split gate:
  email → key on email_hash (block `no_email`/`no_email_consent`/`email_consent_revoked`); voice stays phone.
  Migration idempotent off head.

## P0-3 — Voice executor no idempotency (double-call risk)
- `voice_node_executor.py:38-129`: `begin_step` (L49) then Retell POST (L106-112) with **no dedup**. Docstring
  says fire-and-forget, no Call row written → nothing to dedup against. Success `complete_step(result_code="call_placed")`
  (L128) → `return node.next_node_id`. SMS/email executors same gap (email idempotency_key guards *metering* only).
- **Reusable seam:** `AutomationWorkflowStepExecution` (`models/automation_workflow.py:302-320`) — the "Attempt"
  record — unique `(workflow_run_id, step_id, attempt_number)` (`uq_automation_step_execution_attempt`). Created by
  `AutomationWorkflowRuntimeService.begin_step` (`runtime_service.py:48-79`).
- **Fix plan:** before the Retell POST, check for an existing completed StepExecution for `(run.id, node.id)` with
  `result_code="call_placed"` (a prior attempt already dialed) → skip re-dial, advance. Reuse the attempt ledger;
  no new table. Consider applying the same guard shape to SMS/email (defense-in-depth) — but voice is the P0.

## Plan 12 substrate

### A. ComplianceGateService (`compliance_gate_service.py`) — the single pre-dispatch gate
- Protocol `ComplianceGate.check(run, channel_type) -> GateResult` (`compliance_gate.py`); `GateResult(action=allow|block|hold, reason, retry_at)`.
- Ordered checks: emergency halt (`OutboundEmergencyHalt`) → quiet hours (`QuietHoursService`) → contact/consent.
- Invoked at dispatch in `step_dispatcher.py:157` for ALL send nodes (`SendSms/Voice/EmailNode`, L138). block→fail; hold→timer+wait (held, not dropped); allow→executor. Real gate injected via `for_run` factory (L387).
- **No content/PHI check at dispatch** — content lives only in the publish-time validator seam (below).

### B. Validator seam (`validation_service.py`)
- `ContentComplianceValidator` Protocol L52-61: `validate(definition, *, institution_id, location_id) -> list[ValidationIssue]`.
  Receives the **whole `WorkflowDefinition`**. `NoOpContentValidator` L64 returns `[]`.
- `WorkflowValidationService` injects `content_validator` (default NoOp) + `readiness_checker`. Existing
  `_consent_and_content` guardrail L178-209 (marketing classes `{"sales","marketing"}` L40) already blocks send-with-no-consent-path.
- **Publish call site:** `definition_service.py:157-164` `publish_version` constructs `WorkflowValidationService(session, readiness_checker=ChannelReadinessService(...))` — **does NOT pass content_validator** (uses NoOp). Fail-closed on error severity → 422. Also behind builder `/validate` endpoint (`automation_workflows.py:148-160`).
- Validation runs **publish-time only** (+ /validate), NOT at dispatch.
- **Fix plan:** implement real `ContentComplianceValidator`; inject at publish site (mirror readiness_checker). Content-class per version.

### C. Consent/suppression/DNC models (`models/sms_consent.py`)
- All scope by `institution_id` (NOT NULL); `location_id`/`contact_id` nullable, **not in any unique index → enforcement is per-institution, not per-location.**
- `ConsentRecord`: `(institution, channel, phone_hash)` index. `SmsSuppression`: partial-unique `(institution, channel, phone_hash) WHERE is_active`. `DoNotContact`: partial-unique `(institution, phone_hash) WHERE is_active` — **no channel column** (blocks all channels), no location scope in unique.
- `SmsComplianceService.assert_can_send` (`sms_compliance.py:66-116`): DNC (institution+phone, ignores location/channel) → SmsSuppression (institution+channel=sms+phone) → latest ConsentRecord REVOKED. Writes all hardcoded channel=sms.
- **DNC-tiers fix plan (P7):** DNC is already institution-scoped (that IS the "institution-wide" tier). Add a `scope` dimension (location | institution | group) so a per-location STOP suppresses only that location's sender, while a privileged action sets institution/DSO-wide. Add `channel`-agnostic remains (DNC blocks everything for the scope). Needs migration + query changes in `sms_compliance` + gate.

### D. STOP keywords (`twilio_webhooks.py:40-42`) — module-level sets, extendable
- `STOP_KEYWORDS = {"STOP","STOPALL","UNSUBSCRIBE","CANCEL","END","QUIT"}`, START, HELP.
- Tokenizer `_TOKEN_RE = [A-Z]+` (L46) — **ASCII only**; accented `ARRÊT`/`DÉSABONNER` split around the accent. `ARRET` (unaccented) matches fine.
- **FR STOP fix plan (P6):** add FR keywords (`ARRET`, `ARRÊT`, `DESABONNER`, `DÉSABONNER`, `RETIRER`, `FIN`) and broaden `_TOKEN_RE` to include accented range (e.g. `[A-ZÀ-ÿ]+` on the uppercased body) so accented forms match. Also broaden HELP (`AIDE`) and START (`DEBUT`/`OUI`?) — confirm CASL scope; keep STOP-first precedence.

### E. Migration convention
- Head `20260703_retell_from_number`. Examples: idempotent ADD COLUMN (`20260703_retell_from_number.py`),
  CREATE TABLE + partial index + RLS + grant (`20260703_outbound_halt.py`), CHECK swap (`20260703_consent_channel.py`).

## Sequencing note
Migrations touched by P0-2 (ConsentRecord email_hash) and P7 (DNC scope/channel) both alter `sms_consent`
tables — chain them linearly (P0-2 first off head, then P7 off P0-2) to keep a single head.
