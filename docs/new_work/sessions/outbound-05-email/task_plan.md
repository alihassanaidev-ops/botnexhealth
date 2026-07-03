# Task Plan: Outbound 05 — Outbound Email

## Goal
Replace `_dispatch_send_stub` for `SendEmailNode` with a real email send via
Resend (plain text, v1). Template renderer from Plan 04 is reused.

## Current Status
**Complete** ✅ — all 3 slices shipped.

## Decisions

| # | Decision | Resolved | Notes |
|---|----------|----------|-------|
| D1 | Body format | ✅ Plain text for v1 | HTML deferred until Plan 02 visual builder |
| D2 | From-address | ✅ institution.email_from_address → fall back to settings.resend_from_email | Institution creds from Plan 10 |
| D3 | Missing contact email | ✅ Fail step + fail run | Same fail-safe as Plan 04 SMS |
| D4 | Resend HTTP pattern | ✅ Reuse httpx.AsyncClient from email_notification_service.py | No idempotency key needed for campaign sends |

## Slices

### Slice 1 — EmailNodeExecutor
- [ ] `src/app/services/automation/email_node_executor.py`
- [ ] Loads Contact → `contact.email`; fail step+run if missing
- [ ] Loads Institution → `email_from_address` / `email_from_name` with platform fallback
- [ ] Renders subject + body via `render_sms_body` (same `{{var}}` template renderer)
- [ ] Sends via Resend HTTP API (plain text only)
- [ ] Fail step + fail run on any error

### Slice 2 — Wire dispatcher
- [ ] `step_dispatcher.py`: route `SendEmailNode` → `EmailNodeExecutor`
- [ ] Voice remains on stub

### Slice 3 — Tests
- [ ] No contact email → fail
- [ ] No Resend API key → fail
- [ ] Send success → complete step, return next_node_id
- [ ] Resend HTTP error → fail step + fail run
- [ ] From-address fallback to platform settings

## Files Touched
| File | Change |
|------|--------|
| `src/app/services/automation/email_node_executor.py` | New |
| `src/app/services/automation/step_dispatcher.py` | Route SendEmailNode to executor |
| `tests/unit/test_outbound_email_executor.py` | New |
