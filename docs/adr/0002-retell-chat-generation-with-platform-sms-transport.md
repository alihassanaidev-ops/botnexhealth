# Retell Chat Generation with Platform SMS Transport

## Status

Accepted

## Context

Workflow authors need a stateful SMS step that can handle natural-language patient replies. Retell can either own an SMS number and conversation transport or expose its Chat API as a response generator. BotNexHealth already owns clinic Twilio numbers, signature verification, SMS consent and STOP handling, encrypted message logs, run correlation, quiet hours, delivery callbacks, and staff handoffs. Giving transport ownership to Retell would create a second source of truth for those controls.

Retell chat IDs also have a vendor lifecycle, while workflows need deterministic local inactivity timeouts, hard duration limits, turn limits, idempotency, and behavior after a patient returns later. Exposing those controls and arbitrary PMS field mappings on every workflow node made a transport-level policy look like clinic-authored workflow content.

## Decision

Use Retell's Chat API only as the response-generation adapter. Twilio remains the sole SMS transport.

The `retell_sms_conversation` workflow node's authoring interface contains only an AI SMS agent profile and a next step. It parks a run and creates a local `RetellSmsSession`. The session is authoritative and the Retell `chat_id` is a nullable vendor correlation ID created lazily on the first patient turn. Platform policy fixes inactivity at one hour, hard duration at 24 hours, patient turns at 12, generated replies at three SMS segments, and quiet-hour enforcement. Inactivity advances to the next workflow step without creating a staff handoff; provider or delivery failure creates a staff handoff. When the local session ends, a later unmatched SMS may enroll a new workflow and create a new session and `chat_id`; an ended session is never reopened. For an `sms_reply`-triggered run that parks on this node, the triggering inbound message is queued as the new session's first turn.

Each inbound Twilio message gets a `RetellSmsTurn` idempotency claim before any mutating Retell call. Mutating network timeouts are treated as ambiguous and are not blindly retried. STOP/START/HELP remain deterministic platform-owned controls. Patient phrases are not intercepted by this node. A Retell agent may end its chat and collect `conversation_outcome`; the platform exposes that as `retell_sms_agent_outcome` so a downstream Condition node can branch after termination.

Retell receives only an automatic, allow-listed AI SMS Context resolved through the same provider-neutral merge context for NexHealth and GoTracker. It includes available patient first name and preferred language; clinic identity, phone, and timezone; relevant appointment, booking, recall, callback, and prior-message values; and the workflow conversation goal. Authors cannot add mappings. Internal institution/location/run/session IDs are sent as Retell metadata, not prompt variables. Full workflow context and raw PMS deliveries are never forwarded.

## Consequences

- Existing Twilio compliance, audit, retention, and delivery behavior applies to generated replies.
- Local expiry remains deterministic even if Retell's chat status is delayed or unavailable.
- Workflow authors cannot weaken lifecycle, billing, failure, or disclosure safeguards per node.
- The platform must persist profiles, sessions, and turn claims and run a background turn processor.
- Retell still stores chat content according to the configured Retell agent/account retention and PII settings; operators must configure those settings consistently with clinic policy.
- A response generated successfully by Retell but lost during an ambiguous network response is handed to staff rather than regenerated, favoring at-most-once patient messaging.
