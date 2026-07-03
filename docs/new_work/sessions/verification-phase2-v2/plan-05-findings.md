# Plan 05 — Outbound Email — Verification Findings

Audited: 2026-07-03. Evidence from code, not session docs.

## Plan intent (from `docs/new_work/Implementation Plans/05-outbound-email.md`)
Ambitious 9-component build: `email_sending_profiles`, `workflow_email_templates`,
`workflow_email_attempts` data models; `WorkflowEmailActionService`,
`EmailSendingProfileService`, `ResendCampaignClient`, `EmailWebhookService`;
per-tenant sending domain/creds; branded HTML templated rendering; bounce/complaint
webhook + suppression; unsubscribe (non-deferrable legal minimum); cross-channel
suppression (consumed from Plan 12); idempotent dispatch; metering/usage; analytics;
domain verification + reputation warm-up (Plan 10).

## What was ACTUALLY built (session scope was drastically narrowed)
Session `task_plan.md` states the real goal: "Replace `_dispatch_send_stub` for
`SendEmailNode` with a real email send via Resend (plain text, v1)." Only 3 slices.

### 1. `EmailNodeExecutor` — `src/app/services/automation/email_node_executor.py` (126 lines)
- Resolves contact email (`contact.email`, decrypted property) — fail step+run if missing
  (`:50-65`).
- From-address: `institution.email_from_address` → `settings.resend_from_email` fallback
  (`:67-73`). `from_name` from institution.
- Uses **platform-level** `settings.resend_api_key` (`:75`) — NOT per-tenant. Fails with
  `resend_not_configured` if unset (`:76-79`).
- Renders subject + body via `render_sms_body(...)` — the **SMS** template renderer reused
  for email (`:17`, `:89-90`). Plain text only.
- Sends via inline `httpx.AsyncClient` POST to `https://api.resend.com/emails` with
  `text` payload only, optional `reply_to` (`:92-113`). No HTML.
- Fail-safe: any exception → `fail_step(send_failed)` + `fail_run` (`:115-122`).
- Success → `complete_step(result_code="sent")` (`:124`). **No attempt record persisted** —
  only the step's result_code carries "sent".

### 2. Dispatcher wiring — `src/app/services/automation/step_dispatcher.py`
- `SendEmailNode` routed to `EmailNodeExecutor` at `:135-138`.
- Compliance gate checked BEFORE send for all send nodes (`:117-130`): `block`→fail,
  `hold`→complete_run(outcome="compliance_hold"). Voice still on `_dispatch_send_stub`
  (`:140`).

### 3. Compliance gate (Plan 12, consumed here) — `compliance_gate_service.py`
- `ComplianceGateService.check` covers email: emergency halt (block), quiet hours (hold),
  explicit consent for `send_email` via `ConsentChannel.EMAIL` (`:79-80`).
- **Wired only in the Celery path**: `tasks/automation_workflow.py:184,326` pass
  `gate=ComplianceGateService(session)`. The synchronous API advance path
  `api/routes/automation_workflows.py:463` passes **no gate → defaults to NoOpComplianceGate**
  (`step_dispatcher.py:66`). So manual/API-triggered advances bypass compliance entirely.
- The email executor itself performs NO suppression/consent/quiet-hours check
  (grep: zero matches) — fully reliant on the gate.

## MISSING vs plan (the large majority)
- `email_sending_profiles` model/table + `EmailSendingProfileService` — ABSENT. No
  per-tenant API key, no sending domain, no DKIM/SPF/DMARC status, no domain verification.
  (models dir has only `email_template.py`, `sms_consent.py`, `user_email_notification_preference.py`.)
- `workflow_email_templates` model — ABSENT. Email subject/body come from the node's
  `subject_template`/`body_template` inline fields rendered with the SMS renderer.
- `workflow_email_attempts` model/table — ABSENT. No delivery/bounce/complaint status
  tracking, no provider message id stored, no idempotency key persisted, no cost/usage fields.
- `ResendCampaignClient` — ABSENT. Inline httpx call with platform key; no tenant-specific
  key/domain support.
- `EmailWebhookService` + provider webhook route — ABSENT. grep for bounce/complaint/resend-webhook
  found no email webhook route in `src/app/api/routes/`. No signature verification, no
  delivered/bounced/complained event mapping, no suppression-record creation on hard bounce/complaint.
- **Unsubscribe link/token/email suppression — ABSENT.** Plan flags this as a non-deferrable
  legal minimum (CASL/CAN-SPAM, Gap 12). grep "unsubscribe" hits only SMS STOP
  (`twilio_webhooks.py`) — nothing email-side. This is the most serious compliance gap.
- HTML/branded rendering — ABSENT (plain text only; D1 deferred HTML to Plan 02).
- Idempotent dispatch — ABSENT (D4 explicitly decided "no idempotency key needed"). Webhook
  idempotency also moot since no webhook.
- Metering/usage hooks — ABSENT.
- Analytics aggregation (sent/delivered/bounced/complained) — ABSENT.
- Domain provisioning + reputation warm-up — ABSENT (Plan 10 territory; institution
  from-address fields exist but no domain verification/warm-up gating).
- Merge-field allowlist for email — NOT email-specific; inherits whatever `render_sms_body` does.

## Bugs / implementation gaps
1. **Email consent keyed on phone, not email** — `compliance_gate_service._check_explicit_consent`
   starts with `phone = contact.phone; if not phone: return block("no_phone")`, then looks up
   `ConsentRecord` by `phone_hash` for the EMAIL channel. Result: an email-only contact with no
   phone is **blocked from all email** with reason `no_phone`, and email consent is tracked by
   phone hash rather than email address. There is no email-address-based consent or suppression.
   (Plan 12 code, but directly breaks the email deliverability path.)
2. **Compliance bypass on API path** — API advance (`automation_workflows.py:463`) uses NoOp gate,
   so email sends triggered synchronously skip halt/quiet-hours/consent.
3. **No audit/attempt trail** — a successful send leaves only `step.result_code="sent"`; no provider
   message id, recipient hash, or timestamp captured, so bounce/complaint reconciliation is
   impossible even if a webhook were added.

## Architectural concerns
- Reusing the SMS template renderer for email (`render_sms_body`) conflates two channels the plan
  said to keep separate; no HTML, no email-specific merge-field allowlist / PHI class enforcement.
- Provider client inlined in the executor (no `ResendCampaignClient` abstraction) — hard to add
  per-tenant keys or a second provider later.
- Suppression is entirely deferred to Plan 12's phone-hash consent model; the plan's stated email
  contribution (bounce/complaint → suppression) is not delivered because there is no webhook.

## Technical debt
- Platform-level Resend key means all tenants share one sending identity/reputation — contradicts
  plan's per-clinic domain isolation and warm-up strategy.
- No delivery observability (metrics for sends/bounces/complaints/webhook failures) per plan's
  Deployment Considerations.

## Code quality
- Executor is clean, well-commented, consistent fail-safe pattern mirroring the SMS executor.
- Good separation: dispatcher gate check → executor. `_build_from` helper is tidy and unit-tested.

## Tests — `tests/unit/test_outbound_email_executor.py` (10 tests, all pass)
Ran with test env: `10 passed`. Covers: `_build_from` (3 cases), no contact_id, contact not found,
no email, resend-not-configured, institution from-address used, platform fallback, Resend HTTP error.
All fully mocked (httpx.AsyncClient patched). No integration test, no webhook test, no idempotency
test, no RLS test, no suppression test — because those features don't exist. Coverage matches the
narrow implemented scope only.

## Scope alignment verdict
The shipped work is a competent, well-tested **plain-text MVP send** that unstubs `SendEmailNode`
and correctly leans on Plan 12's compliance gate (in the Celery path). But measured against Plan 05
as written it delivers a small fraction: no sending profiles, no campaign template model, no attempt
log, no provider webhook, no bounce/complaint suppression, no unsubscribe (flagged non-deferrable),
no HTML/branding, no metering, no analytics, no per-tenant domain. Estimate ~20% of plan scope.
The session docs are honest that scope was narrowed to "plain text v1"; the plan document itself
was not updated to reflect the deferrals.
