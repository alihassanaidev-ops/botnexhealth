# Task Plan: Outbound 04 — Outbound SMS

## Goal
Replace `_dispatch_send_stub` for `SendSmsNode` with a real SMS send using
`SmsService`, completing the first end-to-end channel in the automation engine.

## Current Status
**Complete** ✅ — all 4 slices shipped.

## Decisions

| # | Decision | Resolved | Notes |
|---|----------|----------|-------|
| D1 | Template engine | ✅ Regex `{{var}}` substitution | No Jinja dep; simple and safe |
| D2 | Unknown merge vars | ✅ Replace with blank | Don't expose `{{var_name}}` to patients |
| D3 | Send failure (Twilio error) | ✅ Fail step + fail run | Retry deferred to future dead-letter plan |
| D4 | Missing phone on contact | ✅ Fail step + fail run | Can't send without a number |

## Available merge vars (confirmed from campaign_templates.py)
- `{{patient_first_name}}` → `contact.first_name`
- `{{patient_last_name}}` → `contact.last_name`
- `{{patient_full_name}}` → `contact.full_name` (or first + last)
- `{{clinic_name}}` → `location.name`
- Any key from `run.trigger_metadata` dict (e.g. `{{appointment_date}}`)

## Slices

### Slice 1 — Template renderer
- [ ] `src/app/services/automation/template_renderer.py`
- [ ] `render_sms_body(template, contact, location, context) -> str`
- [ ] Regex `{{var_name}}` → value from merge_vars dict; unknown → blank

### Slice 2 — SmsNodeExecutor
- [ ] `src/app/services/automation/sms_node_executor.py`
- [ ] Loads Contact from `run.contact_id`, fails step+run if missing or no phone
- [ ] Loads InstitutionLocation from `run.location_id`, fails if no `twilio_from_number`
- [ ] Renders body via template renderer
- [ ] Calls `SmsService.send_sms()`
- [ ] On Twilio error: fail step + fail run

### Slice 3 — Wire dispatcher
- [ ] `step_dispatcher.py`: route `SendSmsNode` → `SmsNodeExecutor.execute()`
- [ ] Voice + Email remain on `_dispatch_send_stub`

### Slice 4 — Tests
- [ ] Template renderer: known vars, unknown vars → blank, context passthrough
- [ ] SmsNodeExecutor: no contact → fail, no phone → fail, no from_number → fail, send success, Twilio error → fail
- [ ] Dispatcher: SendSmsNode triggers real executor (not stub)

## Files Touched
| File | Change |
|------|--------|
| `src/app/services/automation/template_renderer.py` | New |
| `src/app/services/automation/sms_node_executor.py` | New |
| `src/app/services/automation/step_dispatcher.py` | Route SendSmsNode to executor |
| `tests/unit/test_outbound_sms_executor.py` | New |
