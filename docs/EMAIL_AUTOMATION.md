# Email automation and shared inbox design

Last verified: 2026-09-02 against the staging configuration, application code,
and AWS account in `ca-central-1`.

## Decision

Amazon SES is the receiving layer for patient replies. Patient sending remains
on Resend until the separate SES delivery/bounce/complaint pipeline and SES
production-access gates are complete; inbound receiving does not require us to
switch the outbound provider.

The target should use one platform inbound subdomain (for example,
`inbound.scalenexus.ai`) with a signed address per conversation, not a separate
SES receipt-rule stack per clinic. Each clinic can still have its own verified
sending identity and SES tenant/configuration set. A signed Reply-To address
routes a patient response back to the exact institution, location, contact, and
workflow run without putting those identifiers in clear text.

## What exists now

| Capability | Application | Staging AWS / UI | Result |
|---|---|---|---|
| Workflow Send Email node | Recipient resolution, templates, Jinja merge fields, consent/DNC gate, durable outbound ledger, retries, breaker, and usage metering | Patient provider is Resend | Sends are recorded and visible in the shared thread |
| Clinic sending identities | Institution/location model, Super Admin provision/remove/activate controls, managed DNS, hourly verification, and audited mutations | Staging provisioning configuration and IAM are defined with activation locked | Identities can be safely onboarded without changing live delivery after deployment |
| SES outbound | SES v2 sender, tenant name, configuration set, and message tags supported | SES account is still in sandbox (200/day, 1/second); no identities or configuration sets | Not production-ready |
| Patient Reply-To | Signed conversation replies plus signed per-location cold-inbox addresses | Staging CDK defines `inbound.staging.scalenexus.ai` | No tenant ids are trusted from unsigned mail |
| SES inbound processing | MIME parsing, quarantine, encrypted persistence, tenant/contact/run routing, wait-resume, forwarding, sender limits, and raw cleanup | CDK defines MX/identity, receipt rule, encrypted S3, SNS/SQS, DLQ, IAM and alarms | Push-based; no mailbox polling or per-clinic receipt rule |
| Shared inbox | Lists both sides of SMS/email threads, assignment, resolution, and recorded in-app email reply | Institution/location receiving controls have their own settings page | Institution and location admins can reply and configure the locations they own |
| Email delivery events | Resend webhook handles bounce/complaint and unsubscribe state | No SES event destination/consumer | SES delivery, bounce, complaint, open, and click events would be lost |
| Reply automation | An `email_reply` wait resumes its owning run; an optional setting cancels the contact's other active runs | Cold workflow trigger and staff-replied trigger remain future primitives | Unknown senders may become Contact leads only when the clinic opts in |

One provider limitation still matters before SES **outbound** is enabled:

- SES `SendEmail` has no client token. The outbound ledger records a `sending`
  row before dispatch and leaves an unknown crash outcome for operator
  reconciliation instead of automatically risking a duplicate patient email.
- Supplying `TenantName` attributes a send to an SES tenant, but tenant-level
  bounce/complaint suppression must be configured explicitly. SES otherwise uses
  account-level suppression; one clinic must not silently suppress another
  clinic's recipient.

### Super Admin onboarding boundary

Super Admin owns the provider-changing operations: provision an institution or
location identity, remove it, and explicitly activate/deactivate live SES
routing. Institution admins can edit only the display name/reply-to address and
request a verification recheck. Provisioning always creates an inactive row;
DNS becoming verified never changes the live provider by itself.

`SES_CLINIC_SENDING_ENABLED` is a deployment-controlled global interlock. Keep
it false until the rollout gates below pass. Super Admin can still provision and
verify identities while it is false, but the activation endpoint refuses the
transition. AWS region, hosted zone, IAM, event destinations and this interlock
remain versioned infrastructure settings rather than dashboard fields.

### Custom SMTP / app passwords

Bring-your-own SMTP is a separate provider option, not another kind of SES
identity. HighLevel asks a sub-account for host, port, username, password/API
key, From name and From email, then requires it to be selected as active. Gmail
and similar providers use an app-specific password when MFA is enabled. It also
documents important losses with generic SMTP, including managed deliverability
and bounce classification: [HighLevel custom SMTP](https://help.gohighlevel.com/support/solutions/articles/155000007765-how-to-add-your-own-email-service-smtp-).

ScaleNexus should add this only behind the durable outbound ledger and provider
health model. The credential must be encrypted at rest, write-only in every API
response, tested with TLS before activation, independently rotatable, and scoped
to one institution/location. The From address must match the authenticated
mailbox. SMTP alone does not provide inbound replies or trustworthy bounce and
complaint events; Gmail/Microsoft two-way mail should ultimately use OAuth, and
other SMTP providers need a supported webhook or separate inbound connection.

ScaleNexus itself should continue using the SES API with its ECS task role—not
create or store SES SMTP passwords. AWS documents that SES SMTP passwords are
region-specific long-lived IAM-derived credentials, whereas the SDK can use the
task's temporary role credentials: [AWS SES SMTP credentials](https://docs.aws.amazon.com/ses/latest/dg/smtp-credentials.html).

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

## Implemented receiving architecture

### 1. Durable outbound message model

An email message/outbox record is created before dispatch with institution,
location, contact, workflow run, subject/body ciphertext, provider, provider
message ID, thread ID, status, attempt count, and a unique idempotency key.
Workflow sends and inbox replies use the same ledger shape.

This supplies crash-safe deduplication and both sides of the inbox. Signed
Reply-To remains the routing authority; provider-specific RFC header correlation
can be added later without weakening tenant attribution.

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

CDK provisions in `ca-central-1`:

- verified inbound subdomain and MX record;
- active SES receipt-rule set with spam/virus scanning;
- encrypted, private S3 bucket with short lifecycle for raw MIME;
- KMS-encrypted SNS/SQS delivery with a dead-letter queue, least-privilege bucket/queue policy,
  CloudWatch alarms, and the three `SES_INBOUND_*` runtime values;
- idempotent consumer keyed by SES/provider message ID.

SES email receiving is available in `ca-central-1`; AWS lists
`inbound-smtp.ca-central-1.amazonaws.com` as the receiving endpoint. Receiving
requires domain verification, an MX record, resource permissions, and an active
rule set: [regional endpoints](https://docs.aws.amazon.com/general/latest/gr/ses.html#ses_region)
and [receiving setup](https://docs.aws.amazon.com/ses/latest/dg/receiving-email-setting-up.html).

### 4. Workflow and inbox product behavior

Current behavior and remaining extensions:

1. Inbox replies and workflow sends use the durable outbound ledger and appear
   in the same thread.
2. Known-contact replies resume an explicit wait and can stop other automation.
3. Signed cold-inbox addresses match a Contact by email hash or create a lead
   only when `allow_new_contacts` is enabled; ambiguous routing is held.
4. Net-new compose, a staff-replied workflow event, and an explicit resume
   control remain extensions; they are not implied by the reply composer.
5. Optional delivery/open/click/bounce workflow conditions follow after event storage is
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

Until outbound SES gates pass, Resend remains the operational patient sender.
Its messages use the ScaleNexus signed Reply-To once inbound receiving is live,
so patient replies still enter the shared inbox.
