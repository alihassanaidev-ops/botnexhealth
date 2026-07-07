# Plan 05 — Outbound Email — Verification Findings

Audited: 2026-07-03. Updated: 2026-07-08 after commits `63b0363` and `945ab9f`.

## Verdict

Plan 05 is **launch-compliant for plain-text transactional/campaign email v1**. It is not a full high-volume
per-tenant email provisioning system.

Built scope:
- `SendEmailNode` sends real plain-text Resend email via `EmailNodeExecutor`.
- Email sends are gated by the shared compliance gate.
- Transactional/care email can send on implied consent when the email identifier is on file.
- Marketing/recall email still requires express recorded consent.
- Email send-time idempotency exists through the runtime `already_sent` guard plus Resend
  `Idempotency-Key: email:{run}:{node}`.
- Usage metering records email sends as volume (`emails=1`) with `$0` cost under product Option B.
- Every campaign email carries a signed one-click unsubscribe link.
- Resend bounce/complaint webhook suppresses future email when Resend reports `email.bounced` or
  `email.complained`.
- Unsubscribe and bounce/complaint suppression write revoked EMAIL `ConsentRecord` rows keyed by
  `email_hash`, so revoked email consent beats implied transactional consent.

## Current Implementation

### Send Path
- `src/app/services/automation/email_node_executor.py`
  - Resolves `Contact.email`; missing email fails the step/run.
  - Resolves institution from-address with platform fallback.
  - Renders subject/body through the existing sandboxed renderer.
  - Sends plain text through Resend.
  - Adds unsubscribe footer.
  - Adds Resend idempotency header.
  - Records usage.

### Unsubscribe
- `src/app/services/email_unsubscribe.py`
  - Signed token binds institution + email hash.
  - Raw email address is never placed in the URL.
- `GET /api/email/unsubscribe`
  - Verifies token.
  - Enqueues email suppression.

### Bounce / Complaint
- `POST /api/email/webhooks/resend`
  - Signature verified; fail-closed in production.
  - Suppresses email on hard bounce/complaint.
  - Institution scoping relies on the echoed `institution_id` tag from the original send.

### Suppression Writer
- `src/app/tasks/email_compliance.py`
  - Runs under the Celery RLS context.
  - Writes revoked EMAIL consent through the channel-generic `SmsComplianceService` helpers.

## Tests

- `tests/unit/test_outbound_email_executor.py` covers executor send/error paths.
- `tests/unit/test_email_compliance.py` covers unsubscribe token behavior, public unsubscribe, webhook
  verification, and suppression dispatch.
- `tests/unit/test_rbac_route_matrix.py` includes the public email compliance endpoints.

## Remaining / Deferred

- **Per-tenant sending domain:** SPF/DKIM/DMARC + reputation warm-up is external/vendor work and overlaps Plan 10.
- **HTML/branded body:** optional polish; plain text v1 is the approved launch-compliant slice.
- **`workflow_email_attempts`:** not required now. Current attribution/usage lives on workflow steps and
  `usage_events`; bounce/complaint suppression does not need an attempts table for the current design.
- **Delivered/open/click analytics:** not required for current scope.
- **Resend event payload staging verification:** webhook institution scoping should be verified against a live
  Resend event payload because it depends on echoed tags.

## Completion Decision

Plan 05 remains below 100% only because the original plan included scale/deliverability infrastructure. The
launch-critical compliance gaps from the audit — unsubscribe and bounce/complaint suppression — are now closed.
