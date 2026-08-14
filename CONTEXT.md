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
