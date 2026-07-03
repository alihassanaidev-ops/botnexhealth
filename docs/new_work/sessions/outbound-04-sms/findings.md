# Findings: Outbound 04 — Outbound SMS

## Dispatcher hook point (step_dispatcher.py:129)
`current_node_id = await self._dispatch_send_stub(run, node)` — all send nodes
go here after the compliance gate passes. Plan 04 replaces this path for
`SendSmsNode` only; Voice/Email remain stubbed.

## SendSmsNode fields
- `id`, `type="send_sms"`, `body_template: str`, `next_node_id: str`
- `respect_quiet_hours: bool = True` (already enforced by ComplianceGateService)
- `max_attempts: int = 1..3` — v1 sends once; retry logic deferred

## Template syntax: `{{var_name}}` double-brace
Confirmed from `campaign_templates.py`. Merge vars:
- `{{patient_first_name}}` → contact.first_name
- `{{patient_last_name}}` → contact.last_name
- `{{patient_full_name}}` → contact.full_name (or first + " " + last)
- `{{clinic_name}}` → location.name
- Any key from `run.trigger_metadata` (e.g. `{{appointment_date}}`)

## Contact.phone — encrypted
`contact.phone` is a decrypting property on `contact.phone_encrypted`.
`contact.full_name` may exist or may need `f"{first} {last}".strip()`.

## SmsService.send_sms() signature
```python
async def send_sms(
    from_number: str,
    to_number: str,
    body: str,
    institution_location_id: str,
    patient_contact_id: str | None = None,
    call_id: str | None = None,
) -> SmsHistoryLog
```
Already handles: compliance check, PHI encryption, HIPAA logging, Twilio call.

## Context dict
`context = run.trigger_metadata or {}` — passed through from the Celery task.
Contains appointment fields, patient metadata, etc. — varies by campaign type.

## Alembic: no migration needed
Pure service-layer change.
