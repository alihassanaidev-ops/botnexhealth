# Progress: Outbound 05 — Outbound Email

## Initial State
- `_dispatch_send_stub` handles SendEmailNode with result_code="stub_dispatched"
- Template renderer exists (Plan 04)
- Institution email_from_address/email_from_name exist (Plan 10)
- Resend HTTP pattern established in email_notification_service.py

## Slices

### Slice 1 — EmailNodeExecutor
- **Status:** complete ✅
- `src/app/services/automation/email_node_executor.py` — loads Contact email + Institution from-address, renders subject + body via `render_sms_body`, sends plain text via Resend HTTP; fail-safe on every error path

### Slice 2 — Wire dispatcher
- **Status:** complete ✅
- `step_dispatcher.py` — `SendEmailNode` routes to `EmailNodeExecutor`; Voice remains on stub

### Slice 3 — Tests
- **Status:** complete ✅
- `tests/unit/test_outbound_email_executor.py` — 10 tests: `_build_from` helper, executor error paths, institution vs platform from-address, Resend HTTP error
- 1188/1188 unit tests passing, no regressions

### Slice 4 — Email compliance closeout
- **Status:** complete ✅
- `src/app/services/email_unsubscribe.py` — signed unsubscribe tokens binding institution + email hash.
- `src/app/api/routes/email_compliance.py` — public one-click unsubscribe and Resend bounce/complaint webhook.
- `src/app/tasks/email_compliance.py` — suppression task writes revoked EMAIL consent under Celery RLS context.
- `src/app/services/automation/email_node_executor.py` — appends unsubscribe footer, sends Resend idempotency header, tags institution, records usage.
- `src/app/services/sms_compliance.py` — channel-generic email consent/suppression helpers.
- `tests/unit/test_email_compliance.py` — unsubscribe + webhook tests.

## Deferred / Not Required
- Per-tenant sending domain (SPF/DKIM/DMARC + warm-up) is external/vendor work and overlaps Plan 10.
- HTML/branded body is optional polish; plain text v1 is launch-compliant.
- `workflow_email_attempts` is not needed for current unsubscribe/bounce suppression design.
