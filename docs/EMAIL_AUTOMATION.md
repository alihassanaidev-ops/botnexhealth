# Email automation and shared inbox design

Last verified: 2026-09-02 against the staging configuration, application code,
and AWS account in `ca-central-1`.

## Decision

Amazon SES is a good fit for patient workflow email and two-way replies, but
changing `PATIENT_EMAIL_PROVIDER=ses` is not a complete implementation. Keep
patient mail on Resend until the outbound event pipeline, inbound AWS resources,
message ledger, and in-app reply path below are deployed and verified.

The target should use one platform inbound subdomain (for example,
`inbound.scalenexus.ai`) with a signed address per conversation, not a separate
SES receipt-rule stack per clinic. Each clinic can still have its own verified
sending identity and SES tenant/configuration set. A signed Reply-To address
routes a patient response back to the exact institution, location, contact, and
workflow run without putting those identifiers in clear text.

## What exists now

| Capability | Application | Staging AWS / UI | Result |
|---|---|---|---|
| Workflow Send Email node | Recipient resolution, templates, Jinja merge fields, consent/DNC gate, retries, breaker, and usage metering | Patient provider is Resend | Sends work; recent staging logs confirmed provider acceptance |
| Clinic sending identities | Institution/location model and admin UI; Resend and SES providers supported | No SES identities are verified | Resend only in practice |
| SES outbound | SES v2 sender, tenant name, configuration set, and message tags supported | SES account is still in sandbox (200/day, 1/second); no identities or configuration sets | Not production-ready |
| Patient Reply-To | Signed address generator and parser implemented | `SES_INBOUND_DOMAIN` is absent | Workflow messages do not enter the inbound router |
| SES inbound processing | MIME parser, spam/virus quarantine, encrypted persistence, tenant/contact/run routing, wait-resume, and staff forwarding implemented | No active receipt rules, inbound S3 bucket, SQS queue, or runtime URLs | Unreachable in staging |
| Shared inbox | Lists SMS/email threads, messages, assignment, and resolution | Email view is inbound-only; no compose/reply | Read-only email handoff, not a CRM mailbox |
| Email delivery events | Resend webhook handles bounce/complaint and unsubscribe state | No SES event destination/consumer | SES delivery, bounce, complaint, open, and click events would be lost |
| Reply automation | An `email_reply` wait can resume a known run | Cold `email_reply` trigger is hidden/unimplemented; no stop-on-response or staff-replied trigger | Only the known-conversation wait model exists |

Two code-level limitations matter before SES is enabled:

- Resend honors the workflow's stable idempotency key. SES `SendEmail` has no
  equivalent client token, so a crash after provider acceptance but before the
  database commit can duplicate an email unless we add an outbound message/outbox
  ledger.
- Supplying `TenantName` attributes a send to an SES tenant, but tenant-level
  bounce/complaint suppression must be configured explicitly. SES otherwise uses
  account-level suppression; one clinic must not silently suppress another
  clinic's recipient.

## What “like GHL” requires

Comparable CRM behavior is more than a send node. HighLevel separates:

1. **Wait for reply** after a workflow email, with a timeout branch.
2. **Customer replied** automation for a known contact.
3. **Inbound email** automation for cold/new email as well as known contacts.
4. **Stop on response**, ending only that contact's active automation.
5. **User/staff replied**, which records human takeover and prevents automation
   and staff from talking over each other.
6. A shared inbox that displays both sides and lets an authorized user reply in
   the existing subject/thread.

HighLevel documents these as separate workflow primitives rather than treating
all inbound mail as the same event: [Inbound Email trigger](https://help.gohighlevel.com/support/solutions/articles/155000007650-workflow-trigger-inbound-email),
[Customer Replied trigger](https://help.gohighlevel.com/support/solutions/articles/155000002677-workflow-trigger-customer-replied),
[Wait action](https://help.gohighlevel.com/support/solutions/articles/155000002470-workflow-action-wait),
and [User Replied trigger](https://help.gohighlevel.com/support/solutions/articles/155000008196-workflow-trigger-user-replied).

## Target architecture

### 1. Durable outbound message model

Create an email message/outbox record before dispatch with institution, location,
contact, workflow run/node, direction, subject/body ciphertext, provider,
provider message ID, RFC `Message-ID`, thread ID, status, attempt count, and a
unique `(run_id, node_id)` send key. The worker claims and sends that row, then
stores provider acceptance. Inbox replies use the same service and ledger.

This supplies crash-safe deduplication, both sides of the inbox, audit history,
and `Message-ID` / `In-Reply-To` fallback when an email client drops the signed
Reply-To address.

### 2. SES outbound and reputation

- Verify a platform domain and, where clinics want their own From address, a
  clinic-controlled domain/subdomain with Easy DKIM, SPF/custom MAIL FROM, and
  DMARC alignment.
- Request production access and appropriate quotas only after bounce and
  complaint processing is live. AWS restricts sandbox accounts to verified
  recipients, 200 messages per day, and 1 message per second:
  [SES sandbox documentation](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html).
- Provision an SES tenant and configuration set per clinic identity. Explicitly
  configure tenant-level suppression for bounce and complaint reasons:
  [tenant suppression documentation](https://docs.aws.amazon.com/en_en/ses/latest/dg/sending-email-suppression-list-tenant-level.html).
- Publish delivery, bounce, complaint, reject, and delay events to an
  EventBridge/SNS-to-SQS consumer. Opens and clicks should be optional because
  tracking pixels and rewritten links are not needed for every clinical message.
  SES exposes all of these event types through configuration-set destinations:
  [event destination documentation](https://docs.aws.amazon.com/ses/latest/dg/event-destinations-manage.html).
- Ramp volume gradually per sending domain and keep transactional and marketing
  traffic separable so promotional reputation cannot block appointment mail.

### 3. SES inbound

Provision with CDK in `ca-central-1`:

- verified inbound subdomain and MX record;
- active SES receipt-rule set with spam/virus scanning;
- encrypted, private S3 bucket with short lifecycle for raw MIME;
- SNS/SQS delivery with a dead-letter queue, least-privilege bucket/queue policy,
  CloudWatch alarms, and the three `SES_INBOUND_*` runtime values;
- idempotent consumer keyed by SES/provider message ID.

SES email receiving is available in `ca-central-1`; AWS lists
`inbound-smtp.ca-central-1.amazonaws.com` as the receiving endpoint. Receiving
requires domain verification, an MX record, resource permissions, and an active
rule set: [regional endpoints](https://docs.aws.amazon.com/general/latest/gr/ses.html#ses_region)
and [receiving setup](https://docs.aws.amazon.com/ses/latest/dg/receiving-email-setting-up.html).

### 4. Workflow and inbox product behavior

Implement in this order:

1. Inbox email reply/compose using the durable outbound service; include subject
   threading and human takeover state.
2. Known-contact customer reply event and automatic stop/pause-on-response.
3. Staff replied event and an explicit “resume automation” control.
4. Cold inbound addresses and trigger. Unknown senders should create or match a
   contact only under an explicitly configured clinic mailbox; ambiguous clinic
   routing must remain unroutable, not guessed.
5. Optional delivery/open/click/bounce workflow conditions after event storage is
   reliable.

## Rollout gates

Do not switch staging or production to SES until all of these pass:

- a real external mailbox receives a workflow message from a verified identity;
- its reply appears once in the correct clinic/contact/thread and resumes only
  the intended waiting run;
- a staff reply appears in the same thread and the recipient receives it once;
- bounce and complaint events update suppression and workflow analytics;
- duplicate SQS delivery and worker crash/retry do not duplicate inbound or
  outbound messages;
- cross-institution reply tokens, sender mismatch, malformed MIME, spam, virus,
  oversized bodies, attachments, auto-replies, and loops fail closed;
- alarms cover queue age/DLQ, bounce and complaint rates, send throttling, and
  receipt-rule failures;
- SES production access and quotas are approved in the same region used by the
  application.

Until then, Resend remains the operational patient sender and replies continue
to the clinic's ordinary reply-to mailbox rather than the ScaleNexus inbox.
