# BotNexHealth Domain

BotNexHealth is an AI voice and engagement platform for dental clinics. This glossary defines the shared language used across patient lookup, scheduling, PMS integrations, and automation workflows.

## Language

**Practice Management System (PMS)**:
The clinic's source system for patients, providers, schedules, and appointments.
_Avoid_: EHR as the umbrella term, EMR as the umbrella term

**PMS Integration**:
The connection that translates BotNexHealth patient and scheduling workflows to a specific PMS or PMS aggregator.
_Avoid_: Adapter when discussing product behavior, connector

**Bookable Slot**:
A specific appointment time that can be offered to a patient after PMS availability and clinic rules are applied.
_Avoid_: Availability, working hour

**Working Window**:
A provider or operatory schedule window during which appointments may be permitted. A working window is not itself a bookable slot.
_Avoid_: Availability

**Webhook Subscription**:
A PMS-side registration that determines which event payloads are delivered to BotNexHealth.
_Avoid_: Webhook endpoint when referring to the event subscription

**REST Cutover**:
The migration of outgoing PMS API requests from one selected API contract to another.
_Avoid_: URL migration

**API Contract Target**:
The internal normalized API version target used to derive PMS request headers and version-specific resource paths.
_Avoid_: Raw version string

**Webhook Cutover**:
The replacement of PMS webhook subscriptions so delivered event payloads use the target API contract.
_Avoid_: REST cutover

**Webhook Shadow Capture**:
Receipt and storage of webhook deliveries for validation without triggering patient, appointment, or automation behavior.
_Avoid_: Dry run when referring to real provider deliveries

**Shadow Webhook Subscription**:
A provider webhook subscription created only to validate a target payload contract, not to drive live workflow behavior.
_Avoid_: Live subscription

**Business Event Identity**:
The stable identity of a PMS event as a clinic-facing appointment or patient change, independent of which webhook subscription version delivered it.
_Avoid_: Delivery ID when deduplicating workflow effects

**Workflow SMS Node**:
A reusable workflow step that sends a text message through the clinic's configured messaging channel. It is not owned by a specific PMS integration.
_Avoid_: GoTracker SMS node, campaign-only SMS node

**Wait Node**:
A workflow step with an explicit wait mode. `time` parks until a scheduled instant; `sms_reply` parks until a patient reply is mapped or its response window expires. Additional event modes can be added without creating separate top-level node types.
_Avoid_: Hidden Send SMS response setting, separate Wait for SMS Reply node

**SMS Reply Trigger**:
An event trigger that starts a workflow from an unmatched inbound patient SMS after compliance keywords and existing run-scoped replies are handled.
_Avoid_: Global SMS listener without tenant/location scoping

**Campaign Conversation Thread**:
The patient conversation scoped to one workflow run, carrying that run's outbound messages and patient replies for staff review or automation decisions.
_Avoid_: Global patient conversation when discussing run-specific campaign replies

**Retell-backed SMS Conversation**:
A run-scoped patient SMS conversation in which Retell generates reply text while BotNexHealth owns Twilio transport, compliance, correlation, local expiry, and workflow advancement.
_Avoid_: Retell-managed SMS, Retell SMS transport, using `chat_id` as the local conversation identity

**AI SMS Context**:
The provider-neutral patient, clinic, appointment, and workflow values supplied to a response-generating SMS agent. It is resolved from BotNexHealth records rather than exposing a PMS delivery verbatim.
_Avoid_: Raw NexHealth payload, raw GoTracker payload, client-authored dynamic-variable map
