# Plan 12 — Compliance, Consent & Access-Control — Verification Findings

Audited: `docs/new_work/Implementation Plans/12-compliance-and-consent.md`
Session: `docs/new_work/sessions/outbound-12-compliance/`
Date: 2026-07-03. Evidence via graphify + raw file reads.

## Executive summary

The plan is a **large, foundational compliance substrate** (7 major deliverables + ~8 services).
What actually shipped is a **thin slice**: an emergency-halt kill-switch, a 3-channel
`ConsentChannel` enum expansion, and a single `ComplianceGateService` performing 3 checks
(halt → quiet hours → consent). The session docs (`task_plan.md`, `progress.md`) are honest that
scope was deliberately narrowed to "halt → quiet hours → channel consent" — but that narrowing
leaves the **majority of the plan unbuilt**, and the piece that shipped has a real gate-bypass path
and an email-consent bug.

The gate IS invoked before dispatch — but only on the Celery execution paths, not the inline
API enroll path.

---

## Plan deliverables vs reality

| # | Plan deliverable | Status | Evidence |
|---|---|---|---|
| 1 | Multi-channel consent + suppression model, **per-location scoped** | PARTIAL | `ConsentChannel` enum + CHECK constraints expanded; suppression/DNC still institution-scoped, phone-keyed |
| 2 | Content-class + PHI validator (publish + dispatch) | MISSING | no `ContentComplianceValidator`, no `workflow_content_class`, no PHI detection anywhere |
| 3 | Frequency capping (healthcare-exemption caps) | MISSING | no `FrequencyCapService`, no `contact_frequency_ledger` table |
| 4 | AI-voice consent / in-call disclosure / opt-out | MISSING | no `AiVoiceConsentService`, no prompt injection |
| 5 | Emergency compliance halt for **in-flight runs on a workflow version** | PARTIAL | halt is institution-wide gate flag; does NOT cancel timers / terminate in-flight runs; not version-scoped |
| 6 | Spend / blast-radius controls | MISSING | no `BlastRadiusService`, no enrollment ceiling / spend cap |
| 7 | New RBAC permissions for campaign authoring | MISSING | halt endpoints reuse existing `_InstitutionAdmin`; no new perms |
| — | `ComplianceGateService` single pre-dispatch gate | DONE | `src/app/services/automation/compliance_gate_service.py:34` |
| — | `QuietHoursService` (tz-aware, DST-correct) | PARTIAL | no dedicated service; logic inline in gate `_is_quiet_hours` L101-136; DST-correct via `ZoneInfo`; reuses `LocationOperatingHours` |
| — | Bilingual EN/FR STOP keywords | MISSING (EN only) | `twilio_webhooks.py:29` STOP_KEYWORDS English only, no ARRET/ARRÊT |
| — | `ConsentService` / `SuppressionService` | MISSING as named | consent capture only via existing `SmsComplianceService` + Twilio webhook |
| — | Audit logging of config changes | PARTIAL | Twilio keyword suppression audited (`twilio_webhooks.py:94`); halt activate/release NOT audited via AuditService |

---

## What is actually built (with evidence)

### ComplianceGateService — `src/app/services/automation/compliance_gate_service.py:34`
Three sequential checks in `check()` (L40-84):
1. **Emergency halt** (L49): `_active_halt()` L90 — `WHERE institution_id=X AND released_at IS NULL`. Returns `block`.
2. **Quiet hours** (L57): `_is_quiet_hours()` L101 — loads `InstitutionLocation.timezone`, `ZoneInfo(tz)`, `astimezone`, checks `LocationOperatingHours` for weekday. Returns `hold`. **DST-correct** because ZoneInfo handles DST offsets. Falls back to UTC on bad tz; skips (allows) when no location row or no hours configured.
3. **Consent** (L64-84): blocks if `contact_id is None` (`no_contact`) or contact not found. SMS → `_check_sms` delegates to `SmsComplianceService.assert_can_send` (L144-154). email/voice → `_check_explicit_consent` (L156).

### Gate wiring / invocation
- Dispatcher calls gate before EVERY send node: `step_dispatcher.py:118` `gate_result = await self.gate.check(run, node.type)` inside the `SendSmsNode/SendVoiceNode/SendEmailNode` branch. block → fail run; hold → complete_run(outcome="compliance_hold").
- Real gate injected on Celery paths: `tasks/automation_workflow.py:184` and `:326` both pass `gate=ComplianceGateService(session)`.
- **BUG / bypass:** `api/routes/automation_workflows.py:463` constructs `WorkflowStepDispatcher(session, runtime, scheduler)` with **no gate → defaults to `NoOpComplianceGate`** (`step_dispatcher.py:66`). This is the inline enroll path (`advance()` at L465) that runs on run creation with hardcoded `location_timezone="UTC"` (L469). If a workflow's entry node is a send node (no leading Wait), this path fires a send WITHOUT the compliance gate.

### Defense-in-depth (mitigates SMS bypass, not email)
- SMS: `SmsService.send_sms` independently calls `compliance.assert_can_send` (`sms_service.py:126`) → suppression/DNC enforced even when gate is NoOp.
- Email: `email_notification_service` / email node executor perform **zero** consent/suppression checks (grep found none). So on the NoOp enroll path, an email send has **no** consent or quiet-hours enforcement.

### Emergency halt
- Model `OutboundEmergencyHalt` `src/app/models/outbound_halt.py:15`; migration `alembic/versions/20260703_outbound_halt.py` (table + partial index + RLS + grant, append-only no DELETE).
- API `GET/POST/DELETE /automation/workflows/outbound-halt` (`automation_workflows.py:632/664/717`), `_InstitutionAdmin` role, POST idempotent, DELETE 404 if none.
- **Gap vs plan Finding 9:** this only makes the gate return `block` on NEW dispatch attempts. It does NOT cancel pending timers or terminate in-flight runs, and it is institution-wide, not per-workflow-version. Plan explicitly says "Pause ≠ halt … Emergency halt stops in-flight runs on a version." Not met. In-flight runs sitting in a WaitNode timer will still resume and be blocked only at the next gate check (which works), but queued/mid-dispatch attempts are not cancelled.
- Halt activate/release is **not** written to AuditLog (only stored on the row via `halted_by_user_id`). Plan requires "Audit every … halt action with actor attribution."

### Consent schema — `src/app/models/sms_consent.py`
- `ConsentChannel` enum: SMS + EMAIL + VOICE (L16-18). CHECK constraints on `ConsentRecord` (L39) and `SmsSuppression` (L85) now allow `('sms','email','voice')`. Migration `20260703_consent_channel.py` drops/recreates constraints.
- **All three tables (`ConsentRecord`, `SmsSuppression`, `DoNotContact`) key exclusively on `phone_hash`** (L58, L103, L150). There is **no email/address identifier column**.
- `location_id` column exists (nullable) on all three (L51/96/144) but is **not** part of the uniqueness/scope key. Suppression unique index is `(institution, channel, phone)` (`uq_sms_suppressions_active_institution_channel_phone`, L78-81) — **institution-scoped, not per-location** as the plan requires. DNC unique is `(institution, phone)` (L128-131) with no group/institution privileged "remove-me-everywhere" scope tier.

---

## Bugs / implementation gaps

1. **Email/voice consent lookup keyed by phone hash** — `_check_explicit_consent` L163-175 computes `hash_phone(contact.phone)` and queries `ConsentRecord.phone_hash == phone_hash` for the email/voice channel. Email consent has nothing to do with a phone number. Also L163-164: if the contact has no phone, email is blocked with `no_phone` — an email-only contact can never receive email. The schema has no email identifier to fix this properly.
2. **Gate bypass on inline enroll path** — `automation_workflows.py:463` uses NoOp gate (see above). Email sends on this path get zero enforcement.
3. **Hardcoded `location_timezone="UTC"`** on the inline enroll advance (`automation_workflows.py:469`) — wait/quiet-hours timing wrong for non-UTC clinics on that path.
4. **US multi-timezone caveat unaddressed** — plan Technical Considerations flags clinic-TZ-vs-patient-TZ quiet-hours risk (Finding 12); gate uses clinic TZ only, no guardrail.
5. **Quiet hours edge:** boundaries are exclusive (`< open_time`, `> close_time`, L132-135) — sending exactly at close_time is allowed; minor.

---

## Architectural concerns

- **Not a "single shared compliance substrate."** The plan's central architectural decision — one gate + one consent service that Parts 3/4/5 all route through — is only half-realized. SMS keeps its own authoritative path (`SmsService`), email has none, and the gate is not on every dispatch path. Defense-in-depth exists for SMS but the "single authoritative gate" claim is not true.
- **QuietHours is not a reusable service.** Plan 01 names `QuietHoursService` as a dependency; the logic is private to the gate and cannot be reused by the enrollment gate or builder validation.
- **Suppression/consent remain phone-centric.** Extending to email/voice by reusing `phone_hash` is a schema shortcut that will need a real migration (email/address identifier, per-location scope) before email compliance is meaningful.

## Technical debt

- Enum expanded but no data-model support for the channels it advertises (email consent unusable).
- Halt lacks audit-log integration and timer/in-flight cancellation.
- Inline enroll path duplicates dispatcher construction without the gate — easy to forget, already diverged.

## Code quality observations

- Gate code is clean, well-documented, `now` injectable for testability, structural Protocol check at import (L188).
- Halt endpoints are tidy and idempotent.
- Test mock (`_make_session`) matches on `str(stmt)` substring — brittle but functional.

## Tests

- `tests/unit/test_automation_compliance_gate_service.py` — **13 tests, all pass** (verified: `13 passed`). Covers: halt block, no-halt proceed, quiet-hours hold (before open / closed today), skip when no location, allow within hours, no-contact block, SMS suppression block, SMS allow, email no-consent/granted/revoked, protocol conformance.
- Also present: `test_automation_compliance_gate.py` (protocol/NoOp), `test_consent_channel_coverage.py` (constraint coverage), `test_sms_compliance_privacy.py`.
- **Test gaps:** no test for the inline-enroll NoOp bypass; no test that email-only contact (no phone) is handled; no frequency/content/blast-radius/AI-voice tests (features don't exist); no integration test that STOP on one channel suppresses cross-channel; no migration test for legacy sms rows.

## Scope alignment verdict

Delivers roughly **25-30%** of Plan 12 by deliverable count. The shipped slice (gate + halt + enum
+ quiet hours) is functional and tested on the Celery path, but: (a) 5 of 7 major deliverables are
entirely absent (content/PHI validator, frequency cap, AI-voice consent, blast-radius, campaign
RBAC); (b) the emergency halt is a weaker institution-wide flag rather than the in-flight
version-scoped halt the plan specifies; (c) email consent is non-functional due to phone-keyed
schema; (d) a NoOp gate bypass exists on the inline enroll path. As a **foundational Phase-1 peer**
that "must land before any channel sends," it is materially incomplete — email channel in
particular can dispatch without real consent enforcement.
