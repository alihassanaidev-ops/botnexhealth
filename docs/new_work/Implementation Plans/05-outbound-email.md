# Part 5 - Outbound Email Implementation Plan

## What Needs To Be Built

Build sequenced branded outbound email as a workflow action. Emails must render campaign-specific templates, send from the correct clinic/domain identity, handle bounces and complaints, respect cross-channel suppression, track delivery/engagement where available, and feed outcomes into workflow runs.

This extends existing staff and patient notification email into campaign email.

## Existing System Context

The backend already has:

- `EmailNotificationService` sending via Resend.
- `EmailTemplateService` with institution-scoped Jinja2 templates and preview variables.
- `email_templates` table with call/appointment template types.
- Celery notification tasks, retry/dead-letter behavior, and staff recipient resolution.
- PHI-safe email rules: staff emails redacted, patient-facing appointment confirmation gated and inactive by default.

Current gaps:

- Email sending uses platform-level `RESEND_API_KEY` and `RESEND_FROM_EMAIL`.
- Templates are notification-type based, not workflow/campaign-step based.
- No email delivery/bounce/complaint webhook model exists.
- No campaign attempt/log table exists for email.
- No cross-channel suppression or quiet-hours enforcement exists.

## Existing Components To Reuse

- `EmailTemplateService.render(...)` and Jinja sandboxing.
- Existing email template UI patterns in the dashboard.
- `EmailNotificationService` implementation patterns, but not its call-specific payload contract.
- Dead-letter capture and retry classification.
- Audit service for configuration and PHI-sensitive sends.
- Existing notification/SSE infrastructure for progress updates.

## New Components Required

### Data Model

- `email_sending_profiles`
  - `institution_id`, optional `location_id`
  - provider: `resend`
  - encrypted API key or AWS Secrets Manager reference
  - sending domain
  - from name/from email/reply-to
  - DKIM/SPF/DMARC verification status
  - bounce/complaint webhook secret reference
  - `is_active`

- `workflow_email_templates`
  - `institution_id`, `location_id`
  - `workflow_id`, `workflow_version_id`, `step_id`
  - subject, html body, text body
  - approved merge fields/content class
  - active/version metadata

- `workflow_email_attempts`
  - `institution_id`, `location_id`
  - `workflow_run_id`, `workflow_step_id`
  - `contact_id`
  - encrypted recipient email, email hash/masked
  - rendered subject hash and optional encrypted rendered body if retention policy allows
  - provider message id
  - status: `queued`, `sent`, `delivered`, `bounced`, `complained`, `failed`, `suppressed`
  - provider error, timestamps, idempotency key
  - usage/cost fields if provider exposes them

- **Consent/suppression schema is owned by Part 12, not this plan.** The multi-channel consent
  migration (adding `email` to `ConsentChannel`) and the shared suppression/DNC tables are defined
  once in Part 12 (this resolves the earlier overlap where both this plan and Part 4 proposed to
  migrate the same tables). This plan **consumes** Part 12's `ConsentService`/`SuppressionService`
  and **contributes** the email-specific suppression signals: hard bounce and complaint create
  suppression records via Part 12. **Email unsubscribe is a legal minimum (CASL/CAN-SPAM) and is
  not deferrable** — build the unsubscribe link + token + email suppression, wired to Part 12's
  suppression (Gap 12).

### Services

- `WorkflowEmailActionService`
  - validates recipient email and consent/suppression state
  - enforces quiet-hours if configured for email
  - renders campaign template with approved merge fields
  - resolves clinic sending profile
  - sends via provider client
  - records attempt and workflow outcome

- `EmailSendingProfileService`
  - manages per-clinic/per-institution domain setup status
  - verifies provider domain status
  - prevents activation until DNS/domain verification is complete

- `ResendCampaignClient`
  - provider wrapper for campaign email
  - supports tenant-specific API keys/domains
  - returns provider message id

- `EmailWebhookService`
  - receives provider webhooks
  - verifies signatures
  - maps bounce/complaint/delivered events to attempts
  - creates suppression records for hard bounces and complaints

## End-To-End Implementation Approach

1. Add email sending profile storage and setup validation.
2. Add campaign email template model separate from current notification templates.
3. Add email attempt table with workflow linkage and RLS.
4. Implement tenant-aware Resend client.
5. Implement email workflow action service with idempotent attempt creation.
6. Add provider webhook endpoint for delivery, bounce, and complaint events.
7. Integrate hard bounce/complaint with cross-channel suppression policy.
8. Add campaign UI for subject/body editing, preview, test send, and merge fields.
9. Add analytics aggregation for sent/delivered/bounced/complained.

## Architecture Decisions

- Keep campaign email templates separate from `email_templates`. Existing templates are call-notification templates with different variables and privacy assumptions.
- Store provider message ids and outcome metadata, not full provider payloads, unless encrypted raw payload retention is explicitly required.
- Patient-facing campaign email must use approved merge fields and minimum-necessary PHI. Staff notification redaction rules cannot be blindly reused because campaign emails go to patients.
- Sending profile belongs at institution level by default, with optional location override if clinics need distinct domains or identities.
- Bounce and complaint events should update suppression state, not just metrics.

## Technical Considerations

- Jinja rendering must remain sandboxed and should use a strict variable allowlist.
- Render previews must show sample data without PHI leakage.
- Per-domain DNS verification is operationally heavy; UI should surface `pending`, `verified`, `failed`, and required DNS records. **DMARC must be an explicit onboarding step** (Resend handles SPF/DKIM only; Gmail/Yahoo require DMARC alignment).
- **Domain reputation warm-up is a launch-gating deliverability control, not a footnote (Finding 6).** A brand-new per-clinic domain has no reputation; blasting a recall list on day one spam-folders silently while still reporting "delivered." Gate bulk email behind a per-domain warm-up state (ramp volume over ~2–4 weeks) or start clinics on a warmed shared/subdomain and graduate. Add deliverability monitoring (bounce <4%, spam <0.1%). Contradicts a naive "production-grade day one" bulk send.
- Resend webhook signature verification details must be confirmed and implemented before accepting provider events.
- Do not use Celery ETA/countdown for delayed sends. The durable scheduler should dispatch when due.
- Email unsubscribe behavior depends on content class and jurisdiction. Appointment/reminder email may be transactional, but recall/sales qualification may require clearer unsubscribe semantics.

## Dependencies

- Workflow engine and scheduler.
- Cross-channel consent/suppression framework.
- Per-clinic email sending domain provisioning.
- Campaign builder/template editing UI.
- Usage/cost reporting.
- Compliance decisions on email consent and unsubscribe requirements per region.

## Edge Cases

- Contact has no email or unverified email.
- Email domain profile is pending verification.
- Provider accepts send but later emits hard bounce.
- Complaint arrives for a completed workflow run.
- Same patient has STOP for SMS but not email; cross-channel policy says suppress all outbound for that location.
- Template uses a merge field not available for the campaign trigger.
- PHI field is approved for voice but not email.
- Provider webhook arrives multiple times.
- Reply-to inbox receives patient responses outside the app.

## Risks

- Sending from unverified or shared domains can damage deliverability.
- Campaign emails can become marketing under CASL/TCPA-like rules depending on content.
- Bounce/complaint suppression mistakes can either over-contact patients or block legitimate care messages.
- Template flexibility can leak PHI without strict validation.

## Validation Strategy

- Unit tests for merge-field allowlist and template rendering.
- Unit tests for sending profile resolution and inactive-domain blocking.
- Unit tests for bounce/complaint suppression behavior.
- Webhook signature and idempotency tests.
- RLS tests for email profiles, templates, and attempts.
- Integration test for idempotent workflow email dispatch.
- Manual staging test with provider sandbox/domain: send, delivery webhook, bounce simulation, campaign progress update.

## Deployment Considerations

- Launch profile setup before campaign email dispatch.
- Keep existing notification email service stable while campaign email rolls out.
- Add metrics for sends, bounces, complaints, provider failures, profile verification state, and webhook signature failures.
- Add runbooks for DNS verification, high bounce rates, complaint spikes, and provider outage.

## Future Extensibility

- Multiple email providers through a provider interface.
- Location-specific domains and reply routing.
- Email reply ingestion.
- Link tracking through PHI-safe redirect service.
- A/B testing subject lines once campaign analytics mature.
