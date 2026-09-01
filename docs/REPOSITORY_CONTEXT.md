# Repository Context — Onboarding & Integration Guide

> Orientation for a new developer or AI agent. This document deliberately
> **does not repeat** what the existing docs already cover well — it fills the
> gaps and ties the pieces together. Read the cross-referenced docs for depth.
>
> Last refreshed: 2026-08-13. Treat this as the onboarding map, not the live task
> board. Current work state lives in ClickUp; when this doc and code disagree,
> verify against the current code and the focused docs linked below.

## Where to start (existing docs — read these first)

| Read this for | Doc |
|---|---|
| System overview, request lifecycle, RLS, call lifecycle, data model | [ARCHITECTURE.md](ARCHITECTURE.md) |
| NexHealth/PMS: auth, rate limits, slot & booking API, per-PMS caveats | [NEXHEALTH.md](NEXHEALTH.md) |
| Auth, MFA, tenant isolation, PHI encryption, SMS consent, retention | [SECURITY.md](SECURITY.md) |
| HIPAA/PHIPA/PIPEDA readiness (scope, vendors, gaps, policies) | [compliance/](compliance/README.md) |
| Deploy runbook + infra compliance | [DEPLOYMENT_AND_HIPAA_GUIDE.md](DEPLOYMENT_AND_HIPAA_GUIDE.md) |
| Recurring jobs catalog + local debug harness | [SCHEDULED_JOBS.md](SCHEDULED_JOBS.md) |
| Outbound automation/campaign roadmap and current implementation plan | [ROADMAP_OUTBOUND_ENGAGEMENT.md](ROADMAP_OUTBOUND_ENGAGEMENT.md), [OUTBOUND_ENGAGEMENT_IMPLEMENTATION_PLAN.md](OUTBOUND_ENGAGEMENT_IMPLEMENTATION_PLAN.md) |
| NexHealth v3 migration risks and webhook cutover notes | [nexhealth-v3-inbound-compatibility.md](nexhealth-v3-inbound-compatibility.md), [nexhealth-api-version-research.md](nexhealth-api-version-research.md) |
| CDK infra (ECS Fargate, RDS, etc.) | [../infra/README.md](../infra/README.md) |

**This document adds:** the full multi-tenant hierarchy (incl. the
`InstitutionGroup` oversight tier the other docs miss), the complete RBAC
permission model, how each clinic's voice agent is provisioned, the voice-agent
booking orchestration, the automation/GoTracker integration map, the
Twilio/phone-number model, and a consolidated external-services & config
reference.

---

## 1. Product in one paragraph

An **AI voice and engagement platform for dental clinics**. Retell answers the
clinic's phone; our backend gives the agent function calls into a
practice-management system (PMS) for patient lookup, slot search, booking,
cancel, and reschedule. Clinic staff get a web dashboard with per-call
transcripts, summaries, tags, a callback queue, and metrics, plus
email/in-app/SMS notifications. The newer outbound engine adds workflow-driven
appointment campaigns across SMS/email/voice, backed by appointment projections
and PMS writebacks. The platform is **dental-specific** and ships under the
**ScaleNexus** brand.

PMS routing is through the adapter seam in `src/app/pms/`. `pms_type="nexhealth"`
uses NexHealth as the universal PMS layer (see [NEXHEALTH.md](NEXHEALTH.md));
`pms_type="gotracker"` uses the ScaleNexus GoTracker Synchronizer API; and
`pms_type="none"` is call-intelligence-only, with booking/scheduling disabled.

---

## 2. Multi-tenant hierarchy

Three tenancy concepts. The first two are in ARCHITECTURE.md; **`InstitutionGroup`
is not documented elsewhere and is easy to miss.**

```
InstitutionGroup          (e.g. a DSO — read-only oversight tier; optional)
   └── Institution        (one clinic company / tenant root — owns all PHI scope)
         └── InstitutionLocation   (one physical practice/office)
               ├── pms_type-specific location config            → PMS binding
               │    (NexHealth subdomain/location or GoTracker product key)
               ├── retell_agent_id                              → voice-agent binding
               ├── twilio_from_number                           → outbound SMS identity
               ├── operating hours, breaks                      → slot filtering
               └── transfer numbers (per department)            → live-call transfer
```

- **`Institution`** is the tenant root. Every PHI-bearing row carries
  `institution_id`; Postgres RLS enforces isolation (see ARCHITECTURE.md §Multi-tenancy).
- **`InstitutionLocation`** is one physical office. Slugs are unique **per
  institution**, not globally (`src/app/models/institution_location.py`). Each
  location independently binds to its PMS config, its own Retell agent, and its
  own Twilio sender number. For a multi-location institution there is **no
  "default" location** — `location_id` is mandatory on every PMS-touching route,
  because guessing would route a booking into the wrong clinic's PMS
  (`src/app/pms/factory.py`).
- **`InstitutionGroup`** (`src/app/models/institution_group.py`) models a parent
  org (e.g. a **DSO** that owns several clinic companies). It exists **only** to
  power the read-only `GROUP_ADMIN` oversight role across member institutions (see
  §3). It carries no PHI of its own.

**A DSO with multiple clinics** maps to: one `InstitutionGroup` → several
`Institution`s → each with its `InstitutionLocation`s.

---

### 2.1 One person identity, two staff views

`Contact` is the platform's canonical local person record. A lead, caller and
patient are not three tables that must be converted between:

- **lead** — `nexhealth_patient_id IS NULL` and `lead_status IS NOT NULL`;
- **contact** — no PMS id and no lead lifecycle (typically an inbound caller);
- **patient** — the same row after a PMS id is attached. Despite the historical
  column name, the id may come from either NexHealth or GoTracker.

The dashboard deliberately exposes only two person directories. **Contacts**
is the relationship workspace for non-PMS people (leads and callers), including
manual entry, consent, notes and call history. **Patients** contains only
PMS-linked people and is shown only to PMS-connected tenants. It reads the local
projection maintained by signed NexHealth/GoTracker patient webhooks rather
than making a live vendor request for every page view. That keeps paging and
location scope deterministic and leaves the directory usable during a vendor
outage; the PMS remains the system of record.

The former `/enquiries` page redirects to `/contacts`. The legacy
`campaign_enquiries` table and `/institution/enquiries` API remain temporarily
for expand/contract and campaign compatibility, but active intake creates or
matches `Contact` rows. When a location admin enters a contact manually, the
backend pins it to their assigned location and creates the same
`ContactLocationAccess` grant used by webhook and call ingestion.

---

## 3. RBAC & permission model

> SECURITY.md carries the high-level authorization model. This section keeps the
> detailed role inventory, dependency-function list, and route matrix in one
> place for code navigation.

### 3.1 Roles (`UserRole`, `src/app/models/user.py`)

| Role | Scope field(s) set | Powers |
|---|---|---|
| `SUPER_ADMIN` | none (`institution_id` NULL) | Platform operator; only cross-tenant principal; all `/admin/*` |
| `INSTITUTION_ADMIN` | `institution_id` | Admin over one institution & all its locations |
| `LOCATION_ADMIN` | `institution_id` + `location_id` | Admin over **one** location (pinned) |
| `STAFF` | `institution_id` + `location_id` | Non-admin user of one location (pinned) |
| `GROUP_ADMIN` | `group_id` only | **Read-only** oversight across an `InstitutionGroup`; confined to `/group/*`; walled off from all PHI/setup/write/call routes |

`GROUP_ADMIN`'s `group_id` is mutually exclusive with `institution_id`/`location_id`.
It is **not** a backdoor: MFA is required for it like every other dashboard role,
and every institution/location/PHI dependency intentionally excludes it.

### 3.2 Two enforcement layers

1. **Role gates** (`src/app/api/deps.py`) — FastAPI dependencies that raise 403 on
   role mismatch. Use these to require a role on a route:
   - `get_current_super_admin` (alias `get_current_admin`) — SUPER_ADMIN
   - `get_current_institution_admin` — INSTITUTION_ADMIN
   - `get_current_location_admin` — LOCATION_ADMIN (+ requires non-null `location_id`)
   - `get_current_institution_or_location_admin` — either admin
   - `get_current_location_staff_or_admin` — LOCATION_ADMIN or STAFF
   - `get_current_institution_or_location_user` — the broad "portal user" gate (all 3 institution roles)
   - `get_current_group_admin` — GROUP_ADMIN (+ requires non-null `group_id`)

2. **Scope pins** (`src/app/api/deps_scope.py`) — for location-scoped roles
   (`LOCATION_ADMIN`, `STAFF`), `require_location_scope()` is a dependency factory
   that extracts the target location from path/query/body (resolving
   `loc_slug`/`location_slug` within the user's institution) and **403s if it
   differs from the user's pinned `location_id`**. Non-location roles are a no-op.

The auth dependency also sets the Postgres **RLS context** for the request
(`RlsContext.for_user(user)`), bridging the role model to the DB-level isolation
described in ARCHITECTURE.md / SECURITY.md.

### 3.3 "How a location user only sees their contacts and patients" — `ContactLocationAccess`

This is the non-obvious part. A location-scoped user does **not** see every
contact in the institution. Visibility is granted per-contact via the
`contact_location_accesses` junction table
(`src/app/models/contact_location_access.py`, unique on `contact_id`+`location_id`):

- Grants are **auto-created on call ingestion**: when a call resolves to a
  location, `post_call_service.py` upserts a `(contact_id, location_id)` grant.
- Signed PMS patient projection and manual contact entry create the same grant;
  a location-admin write is pinned to the account's assigned location.
- Contact reads filter through it (`src/app/api/routes/contacts.py`): a
  location-scoped user requesting a contact with no grant row gets **404, not 403**
  (existence is hidden). `INSTITUTION_ADMIN` (no `location_id`) bypasses the filter
  and sees all institution contacts.

### 3.4 Role → route-group matrix (derived from router guards)

| Route group | Allowed roles |
|---|---|
| `/admin/*` (institutions, users, groups, twilio, sms, platform dead-letter view) | **SUPER_ADMIN only** |
| `/institution/undeliverables` (Automation issues page) | INSTITUTION_ADMIN, LOCATION_ADMIN (retry additionally requires `write:replay`) |
| `/group/*` | **GROUP_ADMIN only** |
| `/institution/setup`, `/institution/statuses` | INSTITUTION_ADMIN, LOCATION_ADMIN |
| `/institution/email-templates`, `/custom-fields`, `/notification-recipients`, dashboard mutations | **INSTITUTION_ADMIN only** |
| `/institution/contacts` (create/update; merge non-PMS contacts only) | INSTITUTION_ADMIN, LOCATION_ADMIN |
| `/institution/sms` | all 3 institution roles |
| `/institution/*` portal (reads) | all 3 institution roles + `require_location_scope()` |
| `/institution/calls/{id}/notes` | all 3 institution roles (see §3.5) |

`institution_portal.py` is the mixed-tier file: same prefix, individual routes
step up from "any portal user + location pin" to "institution_admin only"
depending on sensitivity — check the per-route `dependencies=` when editing it.

The super-admin user directory (`/admin/users`) is also the cross-tenant
onboarding surface for institution and location admins. An institution admin is
assigned an `institution_id` with no location pin; a location admin is assigned
both an `institution_id` and one `location_id` that must belong to that
institution. Super admins can edit those assignments later from the same page;
the API revalidates the location/institution relationship on both invite and
edit.

### 3.5 Call notes — staff free text on a call

`call_notes` is the one place clinic staff type prose about a call
(`src/app/models/call_note.py`, routes at the end of
`src/app/api/routes/calls.py`). It renders as a message-style thread under the
triage details in the call detail panel, for **every** tenant — NexHealth,
GoTracker and no-PMS alike.

Three properties are load-bearing:

- **It inherits the call's scope, never widens it.** `institution_id` and
  `location_id` are copied from the parent call at write time so the
  `call_notes_rls` policy mirrors the `calls` policy without a join. Every
  handler loads the note through `_get_scoped_call()` first, so a thread only
  exists for someone who can already open the call: an INSTITUTION_ADMIN sees
  every note in their institution, a LOCATION_ADMIN or STAFF sees notes on
  their location's calls.
- **The body is PHI.** Staff will type patient details into it, so
  `body_encrypted` is AES-256-GCM at the application layer like
  `Call.summary`. Unlike transcripts it is served *inline* rather than behind
  an audited reveal — the people reading a note are the ones who wrote it. That
  trade is why SUPER_ADMIN is refused outright (and the attempt audited), the
  same fail-closed rule `_ensure_phi_reveal_allowed` applies to reveals. Note
  bodies never reach a log or an audit `metadata` blob; only their length does.
- **Authorship is asymmetric.** Only the author may edit a note — an admin can
  remove one but never rewrite it under someone else's email. Delete is soft
  (`deleted_at`), because a note may be the only record of what the clinic did
  about a call. `can_edit` / `can_delete` come back resolved per-caller on the
  API response; the client must not re-derive them.

`author_email` is snapshotted on the row so a thread stays attributable if the
user is later removed. `CALL_NOTE_CREATE` / `_UPDATE` / `_DELETE` audit actions
cover every mutation, including the denials.

---

## 4. The voice agent: Retell

### 4.1 How each clinic gets its own agent — **provisioning is MANUAL**

There is **no code in this repo that creates, duplicates, or configures Retell
agents**, imports phone numbers, or defines tool/function JSON schemas. All of
that is done in the **Retell dashboard**. Admin provisioning calls are
**read-only**, used to pick/verify an existing agent; workflow execution also
uses Retell's create-chat/completion/get/end endpoints for the
`retell_sms_conversation` response-generator node:

- `GET /api/.../retell/agents` → Retell `list-agents` (`admin_institutions.py`)
- `GET /api/.../retell/agents/{id}` → Retell `get-agent/{id}` — *"verify a manually
  entered Retell Agent ID."*
- `GET /api/.../retell/chat-agents` → Retell `list-chat-agents` — populate the
  SMS-profile Chat Agent selector.
- `GET /api/.../retell/chat-agents/{id}` → Retell `get-chat-agent/{id}` — verify
  a selected or manually entered Chat Agent ID.

**Per-location onboarding, end to end:**

1. In the **Retell dashboard**, an operator creates/configures the agent for the
   location: prompt, voice, LLM, the **tool/function schemas** (parameter
   definitions the LLM sees), and the **webhook/function URL** pointing at this
   backend's `POST /api/v1/retell/functions` and the `call_analyzed` webhook.
2. In the **Twilio/Retell dashboards**, a Twilio voice number is assigned to that
   agent (this PSTN→Retell routing is **not** managed in this repo).
3. In **our** admin dashboard/API, an admin records that agent's ID on the
   location: `retell_agent_id` is an optional field on
   `LocationCreate`/`LocationUpdate` and a plain nullable column on
   `InstitutionLocation` (`src/app/models/institution_location.py`). Optionally
   verified via the `get-agent` call above.

So "duplicating an agent for a new clinic" today means: clone it in the Retell
dashboard, point its functions at the same backend URL, assign a number, and
paste the new `agent_id` into the new location record. If you build automated
provisioning, this is the seam to fill (a write path against the Retell API +
populating `retell_agent_id`).

### 4.2 Agent ↔ location binding (runtime, 1:1)

Every inbound function call carries an `agent_id`. The backend extracts it
(`_extract_agent_id`, tolerant of several payload shapes), and
`InstitutionService.get_location_by_retell_agent_id()` resolves it to the active
location+institution (`WHERE retell_agent_id = :agent_id`). That lookup scopes
the entire request, and the same lookup attributes the post-call webhook. The
binding is strictly **1 Retell agent ↔ 1 InstitutionLocation**.

### 4.3 Function dispatch (`src/app/retell/`)

- `POST /api/v1/retell/functions` is the single dispatch endpoint
  (`functions.py`). It is **HMAC signature-verified** using `RETELL_API_SECRET`
  (`src/app/retell/security.py`).
- Handlers register by **name** into an in-process registry via
  `@register_function(name)` (`handlers.py`). The backend validates only the
  function **name** + a loosely-typed `args` dict — the **parameter schemas live
  in Retell**, not in the repo.
- Registered functions:
  - **Read-only:** `list_locations`, `get_location_details`, `lookup_patient`,
    `find_appointment_slots`, `list_appointment_types`, `list_providers`,
    `list_insurance_plans`, `list_transfer_numbers`, `list_operatories`
  - **Mutating (idempotency-wrapped):** `create_patient`, `book_appointment`,
    `cancel_appointment`, `confirm_appointment`, `reschedule_appointment`,
    `reschedule_appointment_v2`
- **Idempotency** (`src/app/retell/idempotency.py`): unique on
  `(call_id, function_name, HMAC(args))`. A Retell retry replays the cached result
  instead of double-booking; in-flight duplicates get a retryable "still
  processing" response.
- **Identity gate:** `lookup_patient` requires the caller-stated patient name,
  date of birth, and an exact full phone number or email match before any patient
  ID is returned. Failed, missing, and ambiguous matches return the same neutral
  no-data response.
- **GoTracker full lookup:** after that identity gate passes,
  `lookup_patient(detail_level="full")` queries the Synchronizer's appointment
  list for the verified `contactId` from the clinic-local current day onward,
  with `exclude_cancelled=true`, and returns a normalized
  `upcoming_appointments` list. Its appointment IDs retain the `gt-` prefix and
  are the only IDs a voice agent may pass to cancellation or rescheduling.
  The initial GoTracker contact lookup likewise sends the caller's name, DOB,
  email, and phone filters to the Synchronizer; Nexus must not search only a
  locally fetched contacts page.
  GoTracker new-patient creation uses the Synchronizer's
  `POST /api/patients/` consumer write-back endpoint through the same shared
  `create_patient` handler; there is no GoTracker-specific Retell tool name.

After the call, Retell posts a `call_analyzed` webhook → the request thread only
verifies the signature, claims an idempotency row, and enqueues a Celery task
(<100 ms response). The worker runs the post-call pipeline. Full lifecycle is in
ARCHITECTURE.md §Call lifecycle. The persisted `Call` row includes Retell's
top-level `disconnection_reason` and no-PMS requested availability; configured
call custom fields can also source values from Retell `custom_analysis_data`.
For no-PMS `needs_booking` calls, the call-ended SMS path still sends the
patient-facing request-received acknowledgement when enabled, and additionally
fans out a separate PHI-light staff SMS alert to active numbers configured in
the no-PMS SMS Preferences page.

### 4.4 Appointment booking flow (voice-agent orchestration)

What the agent's tool calls do, in order, during a booking call — this is the
voice view that stitches the Retell functions to the PMS adapter (NexHealth
details in [NEXHEALTH.md](NEXHEALTH.md)):

1. **Identify the caller** — `lookup_patient` (name + DOB + full phone/email
   gate). If not found and the caller wants to book → `create_patient`.
2. **Scope the request** — `list_appointment_types`, `list_providers`,
   `list_operatories`, `list_insurance_plans` give the LLM the location's
   bookable options (these are a local cache of PMS reference data, synced on
   demand by `sync_service.py`).
3. **Offer times** — `find_appointment_slots` queries the location's PMS adapter,
   fills missing provider names from the location-scoped ScaleNexus provider
   cache, then `slot_filter.py` trims results to the location's operating
   hours/breaks before the agent reads them out.
4. **Book** — `book_appointment` (idempotency-wrapped) writes the booking back
   through the PMS adapter. `cancel_appointment`, `reschedule_appointment`, and
   `reschedule_appointment_v2` handle changes. The legacy reschedule function
   keeps the book-new-then-cancel-old behavior; the v2 function lets supported
   NexHealth stable-v3 PMSes patch the existing appointment in place.
5. **Transfer if needed** — `list_transfer_numbers` returns the location's
   per-department numbers so Retell can transfer the live call (the bridging
   happens on Retell's telephony side, not here).

All of these execute under the location resolved from `agent_id` (§4.2), so a
booking can only ever land in that location's PMS.

---

## 5. Automation workflows, outbound engagement, and GoTracker events

The repo now contains a production-oriented automation workflow engine in
`src/app/models/automation_workflow.py`, `src/app/services/automation/`, and
`src/app/api/routes/automation_workflows.py`. Workflow definitions are authored
as JSON, validated by `definition_schema.py`, published as immutable versions,
and executed as tenant/location-scoped runs with step executions, durable timers,
events, and drip state. The dashboard surfaces this through the workflow builder
and campaign detail pages under `nexus-dashboard-web/`.
Drafts created from the Campaigns page inherit the institution admin's currently
selected location so channel readiness, enrollment, and inbound reply routing all
use the same location-level Twilio number.

Every step attempt records a PHI-safe input snapshot when execution starts and an
output snapshot when it completes, fails, waits, or resumes. These snapshots are
stored on `automation_workflow_step_executions`; unknown context keys are redacted
before persistence. The run timeline also returns the immutable workflow version
used by that run. The builder's **Executions** view uses that version and the
attempt ledger to render the traversed path, per-node status, retries, duration,
result, error, and recorded input/output. Its node inspector defaults to a
client-readable summary, omits internal/redacted fields, explains failures, and
only shows retry history when multiple attempts exist. Raw input/output remains
available under collapsed **Technical details** for administrators. Older step
rows without snapshots retain the legacy timeline projection as a compatibility
fallback.

Campaign detail intentionally keeps execution monitoring compact: its
**Executions** tab lists and filters runs, then links each run to the builder at
`?view=executions&run=<run-id>`. The builder is the single visual inspection
surface for the graph, node attempts, inputs, outputs, errors, and timing. The
former campaign-level **Operations** tab and duplicate timeline drawer were
removed; operational exceptions should become actionable, owner-aware issues
rather than a second read-only execution log.

Workflow node support is declared once in
`src/app/services/automation/node_registry.py`. The registry owns each node
type's outgoing-edge fields and whether it is authorable, executable, and
dry-run capable. Schema graph checks, publish validation, dry-run, runtime
guards, and the builder capability list all consume that contract. The builder
also runs the authoritative backend validator after edits and again immediately
before publish. Publish fails closed on duplicate node IDs, missing edge targets,
unsupported capabilities, reachable execution cycles, and reachable paths that
cannot terminate; unreachable orphan steps remain a warning. Validation issues
include stable codes, node/field attribution, and a recommended fix where one is
available.

Current schema version `1.0` supports triggers such as `appointment_offset`,
`appointment_state_changed`, `recall_scan`, `manual`, `bulk_import`,
`enquiry_received`, `callback_requested`, `patient_status_changed`,
`sms_reply`, and `email_reply`; node types include `wait`, `drip`,
`send_sms`, `send_voice`, `send_email`, `retell_sms_conversation`,
`update_patient_status`, `update_appointment`, `book_appointment`,
`update_gotracker_appointment`, `booking_link`, `patient_registration`,
`json_mapper`, `llm`, `condition`, `switch`, and `exit`.

`book_appointment` is the campaign-controlled booking action: it resolves the
patient from the run contact, renders provider/type/time from context, re-checks
live PMS availability immediately before writing, books through the shared PMS
adapter, and branches to `booked`, `could_not_book`, or `pending`. `booking_link`
is separate: it configures what a patient-facing link may do later, but does not
itself write an appointment.

Two of those triggers are accepted by the schema but are **not offered in the
builder**, because nothing enrols from them yet: `bulk_import` has no import
route, and `email_reply` has no trigger service. (The email *wait* node is fully
wired and is a separate feature.) `update_gotracker_appointment` is likewise kept
for already-published definitions but removed from the palette — new workflows
should use the PMS-neutral `update_appointment`. The excluded set lives in
`UNAVAILABLE_TRIGGER_TYPES` in `nexus-dashboard-web/src/lib/workflow/catalog.ts`,
and `tests/unit/test_workflow_schema_frontend_parity.py` fails if the builder's
TypeScript model drifts from `definition_schema.py` in either direction.

`enquiry_received` is the sales-intake trigger. Public signed form intake and
staff-entered enquiries both go through `enquiry_intake_service.py`; after the
contact write commits, matching active `enquiry_received` workflows are enqueued
through `enquiry_trigger_service.py` with PHI-light trigger metadata. The Sales
Qualification launch template uses that trigger, a Retell SMS conversation,
patient registration, and a restricted booking link.

Patient action links are authenticated by a signed, expiring token that names one
workflow run and action. Because the token does not expose tenant scope, the database
first permits a SELECT-only lookup of that exact run through
`campaign_link_lookup`; the application then reopens the working session with the
resolved institution and location. Public-link routes must use
`get_campaign_link_db_session` after verifying the signature—an ordinary system
session has no workflow-run visibility under RLS.

The `appointment-reminder-24h` launch template is a separate Appointment
Reminder campaign, not a stage of the confirmation campaign. It uses an
`appointment_offset` trigger with attending/scheduled status eligibility, records
a `booking_link` policy limited to `confirm` and `reschedule`, then sends a
two-step SMS ladder with `sms_reply` waits. Deterministic replies route through
`appointment_reminder_reply`: `YES` confirms via PMS-neutral
`update_appointment`, reschedule/cancel/staff tokens create staff follow-up
outcomes, and no reply exits as `no_response`. The dispatcher still revalidates
appointment-triggered runs immediately before every patient-directed send, so a
cancelled or moved appointment is skipped before either reminder SMS can leave.

### Filter expressions

`src/app/services/automation/filter_expression.py` defines one nested boolean
filter language shared by trigger eligibility, condition nodes and switch cases.
An expression is a tree of `{kind: "group", op: and|or|not, children}` and
`{kind: "rule", field, op, value}`, with roughly thirty operators covering
equality, membership, text, numeric comparison, relative time (`before`,
`after`, `within`, `older_than`), presence, arrays, and field-to-field
comparison. The builder renders it with one recursive component
(`components/workflow/FilterEditor.tsx`), so a new operator is added in
`filter-ops.ts` once rather than in each screen.

Two evaluation rules matter. Comparisons coerce across wire types, because
webhook context delivers numbers as strings and datetimes as ISO text; a value
that cannot be coerced yields `False` rather than raising, so a bad filter never
aborts a run. And a missing field never matches anything except the presence
operators, so "the data never arrived" cannot read as "the value did not match".
Date-only values and naive datetimes resolve in the location timezone.

The legacy `ConditionNode` shape (`logic` + `rules`) is deliberately **not**
up-converted. Its equality is exact where the DSL coerces, so rewriting
published rules could change how a live campaign branches; old definitions keep
the original evaluator and the builder offers an explicit opt-in conversion.

### Multi-way branching

The `switch` node routes to the first of up to twenty labelled cases whose
filter matches, falling back to a required `default_next_node_id`. Its ports are
variable-count, which `node_registry.py` now models through `outgoing_list_fields`
— graph validation reports a mis-wired branch as `cases[2].next_node_id` rather
than blaming the node as a whole.

### Trigger eligibility filters

Every trigger accepts an optional `filter`, evaluated against the event context
*before* a run is created (`services/automation/trigger_filter.py`). Deciding
eligibility there rather than in an opening condition node is what stops
ineligible subjects writing a run, a step execution and analytics rows only to
exit at node one; `trigger_appointment_workflows` reports the saving as
`skipped_filter`. A filter that cannot be evaluated is treated as not matching,
because the safe direction on malformed input is to contact fewer people.

The surgery pre-appointment template uses both: its eligibility moved onto the
trigger, and each call attempt's six chained conditions became one switch on
`call_outcome`. The definition went from 48 nodes to 31, and from 22 condition
nodes to 3.

### Trigger location scoping

Trigger matching is location-scoped through
`src/app/services/automation/trigger_lookup.py`, which every trigger service uses:
a workflow with `location_id IS NULL` is institution-wide and matches any
location, and a workflow bound to a location matches only that location (an event
whose location cannot be resolved therefore matches institution-wide workflows
only). This applies to the appointment-offset, appointment-state, recall,
callback, patient-status and SMS-reply paths, and to the recall scanner's
per-location loop. Before this was centralised, only the SMS reply path honoured
it, so an event at one location could enrol a workflow belonging to another
location in the same institution and contact the patient with the wrong clinic's
voice profile, sending number and hours.

### PMS appointment status catalog

GoTracker's appointment dispositions are defined once in
`src/app/pms/gotracker/statuses.py` — id, stable key, label, PMS-neutral
`semantics`, and whether Nexus may write the disposition back. The catalog is
served at `GET /automation/workflows/pms-appointment-statuses` and consumed by the
builder, so labels are not duplicated in the frontend. `NON_ATTENDING_STATUS_IDS`
and the webhook route's status labelling both derive from it. Note that the
`writable` flag is not yet verified against the installed Synchronizer build and
currently reports every status as writable, which preserves prior behaviour.

### Canvas editing

Undo/redo is recorded at the single `applyDef` seam in `pages/WorkflowBuilder.tsx`
rather than per editor, with rapid edits coalesced so one undo steps over a typed
word rather than a character. Applying a definition and recording it are separate
calls (`commitDef` vs `applyDef`) — undo restores a definition that is already in
the history, and re-pushing it would make undo and redo cancel each other. The
history resets on load and on discard, so undo cannot walk back into a draft the
author has just thrown away.

Duplicate, copy and paste rewrite ids through `cloneNodes` in
`lib/workflow/graph.ts`: edges *inside* the copied set are repointed at the
copies, and edges leaving it are cleared rather than left pointing at the
originals — a copy that silently rejoins the original graph is almost never what
"duplicate" means, and a dangling pointer is at least visible in validation.
Every forward pointer must be listed there, which is the same knowledge
`outgoing()` carries; `patient_registration.on_abandoned_node_id` is the one that
is not called `next_node_id`, and a blanket rewrite would miss it. Optional
pointers that leave the copied set drop to `null`, since `""` is not a valid id.

Node search matches id, type and configured content, which is how a graph of
thirty or forty steps stays navigable. Selection is a set: shift/cmd-click
toggles membership and a drag on the pane draws a selection box, while
`selectedId` remains the single node the config panel edits.

Every unconnected port carries a `+` that opens the step picker and wires the
chosen step to that port. Dragging from the handle already worked, but it
requires knowing the handle is draggable and landing the drop on empty canvas;
the affordance every comparable builder offers is the click. The list of ports
comes from `outgoing()`, so a node type with new ports gets its `+` for free —
and the picker shows steps the clinic cannot run as disabled rather than hiding
them, because "email is unavailable here" is useful where "email does not
exist" misleads.

Delete and Backspace remove the selection in one edit, so one undo brings back
everything the keypress removed. Every canvas shortcut is suppressed while the
focus is in a text field, which is what keeps Backspace deleting a character in
a message body rather than the step being edited.

### Timer throughput and fairness

`poll_workflow_timers` drains due timers across several claim rounds within one
beat instead of taking a single fixed batch. A fixed batch on a fixed beat was a
platform-wide ceiling — 50 timers every 30 seconds is ~100/minute for every
clinic combined — so one bulk enrolment took minutes to issue its first steps.
Rounds stop when a batch comes back short, when the tick's budget is spent, or
at a loop backstop, and the poll reports the backlog it left so queue depth is
measured rather than inferred.

`claim_due_timers` over-fetches and then deals round-robin across institutions
(`_round_robin_by_institution`), oldest-first within each, dealing to the tenant
whose longest-waiting work is oldest. Ordering by `due_at` alone handed the
whole batch to whichever tenant enqueued the most, so a 500-patient recall
delayed every other clinic's reminders with nothing in the logs to explain it.
Fairness is not a per-tenant rate limit: a tenant alone with a backlog still
takes the whole batch. Dispatch *priority* is deliberately absent — it is
authored on the enrollment policy, which is not built, and a column nothing
writes is worse than no column.

`send_sms` is run-scoped and only sends the message. The node does **not** carry
an arbitrary recipient number; it sends to the workflow run's `Contact.phone`
from the resolved `InstitutionLocation.twilio_from_number`. New workflow SMS
sends open or reuse one active `CampaignConversationThread` for the workflow run
and channel. The thread links outbound `sms_history_logs`, inbound
`inbound_sms_messages`, normalized `campaign_response_events`, and
`campaign_staff_handoffs` without duplicating encrypted message bodies.

`retell_sms_conversation` is a stateful response-generator node. It parks the
run and owns a local `RetellSmsSession`; Retell does **not** own the phone number
or send SMS. The Twilio webhook keeps transport, signature validation,
STOP/START/HELP processing, consent, encrypted logs, delivery callbacks, and
thread correlation. On the first non-compliance patient reply, a worker lazily
creates a Retell Chat API `chat_id`, submits the inbound text, bounds the agent
reply to the configured SMS segment limit, and sends it through the existing
`SmsService`. `RetellSmsTurn` rows deduplicate Twilio webhook retries and prevent
blind replay after an ambiguous mutating Retell request.

The local session—not Retell—is the lifecycle authority. Platform policy fixes
the inactivity TTL at one hour and resets it after each accepted turn, subject
to a 24-hour maximum-duration cap, 12 patient turns, and three SMS segments per
generated reply. These are not workflow-author settings. Inactivity ends the
session and advances to the next step without creating a handoff; provider or
delivery failure creates a staff handoff. Retell-ended chats and STOP also end
the local session. Cancelling the owning workflow run marks its active Retell
session `cancelled` in the same transaction, releasing the patient/location
guard; a migration repairs sessions stranded by cancellations from older code.
A later unmatched patient SMS can start a new `sms_reply` workflow, which
creates a new local session and a new Retell `chat_id`; terminal chat IDs are
never reused. When that new run parks directly on a Retell SMS node, the
triggering inbound message becomes its first turn—the patient does not need to
text twice. See [ADR 0002](adr/0002-retell-chat-generation-with-platform-sms-transport.md).

Retell dynamic variables are automatic and deliberately allow-listed. NexHealth
and GoTracker deliveries first become the same normalized workflow/merge
context; Retell then receives only available patient first-name/language,
clinic, appointment, booking, recall, callback, conversation-goal, and previous
message values. Workflow authors cannot map arbitrary fields. Internal
institution/location/workflow-run/session IDs go in Retell metadata. The worker
never forwards the entire trigger context or a raw PMS delivery. If the Retell
agent ends the chat with a collected `conversation_outcome`, the runtime exposes
it as `retell_sms_agent_outcome` for a downstream Condition node.

Retell SMS chat profiles are provisioned by a platform operator from
**Superadmin → Institutions → Location → Edit → Retell SMS Profiles**. The
location editor can create, verify, edit, activate/deactivate, or delete an
unused profile and records its display name, selected Retell Chat Agent, and
active state. New sessions always use the agent's latest Retell version;
legacy purpose/version storage is not authorable. Workflow authors only select
active profiles for their current location. `GET /api/retell-sms/profiles` returns
sanitized location-scoped choices to institution users; a superadmin must pass
`location_id` and receives the technical fields needed by the editor. Retell
agent creation and prompt configuration still happen manually in Retell's own
dashboard, and no Retell phone number is required for this SMS mode.

`wait` is the single first-class pause step and has a typed `wait_for`
configuration. `wait_for.type = time` owns duration, calendar, or
appointment-relative timing. `wait_for.type = sms_reply` parks the workflow run
with a timeout timer and owns the response window and deterministic
`response_mappings`; the normal dispatcher then resumes into the next node
(usually a `condition`) after an inbound mapping or timeout. The builder exposes
these as modes inside the same Wait node and authors reply mappings through
structured accepted-reply, context-output, and staff-handoff controls. The JSON
representation remains an internal workflow-definition detail. Future event
waits can use the same public node without mixing their runtime correlation
logic.
Legacy `wait_for_sms_reply`, direct-delay `wait`, and Send SMS response settings
remain accepted as compatibility inputs for already-published definitions.

SMS replies resolve through that thread first, not by resuming every matching
waiting run:

- bare replies such as `YES` only correlate when exactly one active SMS thread
  belonging to a running or waiting workflow run matches the patient/contact
  and location; threads retained for staff handoff after a run becomes terminal
  are excluded from automated correlation;
- when a phone number belongs to multiple contacts (for example, family members
  sharing one number), all matching contacts are considered and the reply is
  correlated only if exactly one reply-eligible thread exists across them;
- deterministic `response_mappings` on the SMS-reply-mode `wait` use whole-token,
  case-insensitive matching and can update workflow context, create a staff
  handoff, or both. Context-updating mappings resume the normal dispatcher;
  handoff-only mappings create the handoff and do not resume;
- for ordinary replies, the Twilio webhook persists the inbound message and
  campaign response event but treats the correlated workflow run as read-only.
  The Celery resume task owns workflow-run metadata updates and advancement under
  its worker database context. STOP is the narrow exception described below;
- ordinary and mapped replies return empty TwiML. Reply acknowledgments are not
  hardcoded; authors must add another Send SMS node when a workflow should send
  a follow-up message;
- PMS writes never happen inside the SMS node or mapping handler. A following
  workflow action such as `update_gotracker_appointment` must perform any PMS
  update.

`sms_reply` is an inbound-SMS trigger. After compliance keywords and existing
run-scoped replies are handled, an unmatched inbound SMS from a resolved contact
can enroll active `sms_reply` workflows for that institution/location. Optional
trigger tokens use the same whole-token matching as reply mappings; empty tokens
mean any non-compliance inbound SMS can start the workflow.

Active thread statuses are `open` and `handoff`. Terminal runs close active SMS
threads unless an unresolved handoff (`open`/`assigned`) still exists, in which
case the thread remains in `handoff` for staff review. A retained handoff thread
does not compete with a later running or waiting run when an inbound reply is
correlated for automation.

The public inbound webhook runs under the tenant/location-scoped `twilio` RLS
context. It has read access to the workflow rows needed for reply correlation.
Its workflow write access is limited to the STOP terminal path: updating the
correlated run and pending/claimed timers, inserting the cancellation event, and
closing the SMS thread. Other workflow mutation remains restricted to the Celery
execution context. Replies requiring staff review emit an `inbound_sms_reply`
in-app notification.

Appointment-triggered campaigns use a disposable working set rather than live PMS
reads on every dispatch:

- `AppointmentWorkingSet` stores the last scheduling state seen for one
  tenant-scoped appointment: start time, status, provider/type IDs, confirmation
  flags, GoTracker status/flow fields, and freshness timestamps.
- `NexHealthProjectionService` maintains that projection from signed
  `/api/v1/nexhealth/webhooks/*` deliveries and event-ledger claims, then
  enqueues matching active workflows.
- `gotracker_webhooks.py` receives signed GoTracker Synchronizer appointment and
  patient events for `pms_type="gotracker"` locations, updates the same working
  set, cancels runs for non-attending/cancelled states, and records writeback
  completion/failure events.
- Location administration owns an explicit GoTracker webhook reconnect operation.
  When a local provider subscription id exists it calls the Synchronizer's
  `POST /api/webhooks/subscriptions/{id}/rotate-secret` operation and stores the
  returned signing secret encrypted. A `404` from rotation means the stored id is
  stale, so reconnect creates a replacement subscription and stores its new id and
  secret. Without a stored provider id it follows the same create path. Reconnect
  commits the credentials immediately after the Synchronizer response; persistence
  failure is a hard reconnect error. Routine location saves and scheduled lifecycle
  reconciliation never rotate healthy secrets.
- `GoTrackerAppointmentWritebackService` tracks ScaleNexus-originated GoTracker
  appointment writes until the synchronizer confirms or fails them. It serializes
  writes per appointment with an advisory lock because GoTracker has one pending
  write slot per appointment.

The clinic-facing **Patients** directory has a different read requirement from
campaign dispatch. It proxies exactly one current page from the selected
location's NexHealth or GoTracker adapter through
`GET /api/v1/pms/patients/page`; credentials stay server-side, active status is
explicit, and upstream errors become a stable 503. Location-scoped clinic users
receive phone/email directly; institution admins receive masked contact fields
until they use the audited per-row reveal action. The page never loads the full
patient roster. NexHealth uses stable-v3 cursors;
GoTracker requests one fixed Synchronizer page. Existing local Contact links are
attached to returned rows only to open call/history detail. **Contacts** remains
the local non-PMS relationship directory, while local patient projections remain
the workflow/webhook working set rather than the Patients page's completeness
boundary.

GoTracker's **on-site synchronizer, local SQL agent, installer, offline queue,
and stored-procedure implementation remain outside this repo**. This repo owns
the PMS adapter contract, the cloud-facing GoTracker adapter/webhooks, and
workflow behavior that must stay PMS-agnostic.

For GoTracker-backed locations, Providers & Scheduling reads the Synchronizer's
PMS-synced per-date working windows rather than synthesizing rows from available
slots. Nexus can set a cloud-only appointment-type override for a stable working
window ID and clear it to restore the provider/operatory standing rules. It must
not create or alter the underlying window: Tracker remains the source of truth
for its date, time, provider, and operatory.

GoTracker can also return derived closed periods when Nexus requests them. They
are the gaps between open Tracker windows (including a positively stated
00:00–24:00 fully closed day), have no writable working-window ID, and are
shown read-only in both the schedule list and calendar so staff can distinguish
a closed chair from a booked one. Calendar closed spans are a separate visual
layer: they are never merged with open windows, never open the linking drawer,
and never affect open-time, working-window, or unlinked-window counts.

GoTracker Reasons are Tracker-native, read-only scheduling references (the
equivalent of NexHealth EMR descriptors). They are synchronized into the shared
reference cache and shown on the Reasons setup page. A Synchronizer-owned
GoTracker appointment type may link to **at most one** reason, matching the
single native reason accepted by a GoTracker appointment write; NexHealth keeps
its independent multi-descriptor appointment-type mapping.

---

## 6. Twilio & phone numbers

Twilio in this codebase is an **SMS integration only**. The inbound **voice**
number → Retell routing is configured externally (Retell/Twilio dashboards); the
repo has no Twilio Voice/TwiML voice wiring.

### 6.1 The three phone fields

- `InstitutionLocation.twilio_from_number` — the location's **outbound SMS sender**
  (E.164). `SmsService.send_sms` rejects any `from_number` that doesn't exactly
  match it.
- `InstitutionLocation.phone` — the clinic's human contact number (used in SMS
  HELP text).
- `InstitutionLocationTransferNumber` (`institution_location_transfer_numbers`) —
  per-department numbers for **live-call transfer**, surfaced to the agent via the
  `list_transfer_numbers` function.

**Number purchasing is manual** — numbers are purchased externally, not via API.
In the Super Admin institution detail page, the Credentials tab stores one
encrypted Twilio account SID/auth token pair for the institution. The Locations
tab then lists SMS-capable numbers from that institution account through
`GET /admin/institutions/{slug}/twilio/phone-numbers`; the selected number is
stored as the location's `twilio_from_number`. Saving an assigned number also
uses the institution credentials to set that Twilio number's inbound message
webhook to `<PUBLIC_API_URL>/api/v1/twilio/webhooks/inbound-sms` with method
`POST`. Existing direct webhook URLs are replaced because selecting the number
is the explicit ownership action; an existing Twilio SMS Application binding
fails with a conflict instead of being silently removed. The location editor
also exposes a reconnect action for reapplying the webhook after a local tunnel
or deployment URL changes. The platform-wide
`GET /admin/twilio/phone-numbers` endpoint remains available for platform account
operations, but it is not used by the location picker.

### 6.2 Webhooks (`src/app/api/routes/twilio_webhooks.py`, prefix `/twilio/webhooks`)

- `POST /inbound-sms` — case-insensitive keyword opt-out/in
  (STOP/UNSUBSCRIBE/… → suppress; START/UNSTOP → release; HELP/INFO → help
  text). Suppression is scoped to the location resolved from the receiving `To`
  number, so the same phone may still receive SMS from another location. Every
  contact sharing that phone is suppressed at the receiving location. In the
  same transaction, STOP cancels the unambiguously correlated active workflow
  run, its pending/claimed timers, and its SMS thread with reason `sms_opt_out`.
  For a shared-phone ambiguity it cancels every active run with an active SMS
  thread for that phone and location, without touching unrelated email/voice-only
  runs. Routes `To` via `twilio_from_number` and replies with TwiML.
- `POST /sms-status` — delivery-status callback; updates the `SmsHistoryLog`.

Both **require Twilio signature validation** (`RequestValidator` against the raw
URL + form): **503** if the secret isn't configured, **401** on missing/invalid
`X-Twilio-Signature`. In ECS, Gunicorn/Uvicorn trusts the CDK
`trustedProxyCidrs` so the ALB's `X-Forwarded-Proto` preserves the public HTTPS
URL Twilio signed. Unmatched location / `MessageSid` → dead-letter.

### 6.3 Outbound SMS

Single chokepoint `SmsService.send_sms` (`src/app/services/sms_service.py`):
enforces the sender number, **gates on consent** (`SmsComplianceService` — a
blocked send logs a `SUPPRESSED` row and never calls Twilio), logs a `PENDING`
row (encrypted body, masked/hashed number), calls Twilio (offloaded via
`asyncio.to_thread`), and updates status. Entry points: admin sync
`POST /admin/twilio/send-sms` (audited) and the async `send_sms_message` Celery
task (`tasks/sms.py`, 5 retries, exp backoff, dead-letters on exhaustion).
Call-triggered patient auto-SMS is enqueued from the post-call pipeline only if
a body + patient phone + `twilio_from_number` are all present. No-PMS
appointment-request staff SMS alerts use their own configured destination list
(`external_sms_notification_recipients`) and never affect NexHealth/GoTracker
confirmation SMS behavior.
Outbound bodies preserve the rendered template text without automatically
prepending the location name. Send SMS nodes default to appending `Reply STOP to
opt out.` when equivalent case-insensitive copy is not already present; workflow
authors may disable that footer per node. Manual/admin SMS entry points retain
the enabled default. STOP suppression itself remains centralized and cannot be
disabled by a workflow.

Every outbound Twilio message gets a delivery callback URL. By default it is
derived as `<PUBLIC_API_URL>/api/v1/twilio/webhooks/sms-status`; the legacy
`TWILIO_SMS_STATUS_CALLBACK_URL` setting remains an optional explicit override.
Twilio posts message state transitions such as `sent`, `delivered`, `failed`,
and `undelivered` to that route, which verifies the Twilio signature and updates
the existing `SmsHistoryLog` by `MessageSid`.

### 6.3.1 Automation issue operator queue

Permanently failed tasks and webhooks are captured in `dead_letter_events` with
an encrypted replay payload and a redacted operator projection. Platform
operators and clinic/location administrators reach the same dashboard page at
`/undeliverables`, labelled **Automation issues** in the UI because this is a
technical background-job queue, not a list of undelivered patient messages. The
page selects the global `/api/admin/dead-letter-events` surface for
`SUPER_ADMIN` and the RLS-scoped `/api/institution/undeliverables` surface for
tenant roles. Location administrators see only their location.

Rows expose whether replay is actually supported. Replay requires the named
`write:replay` permission (institution or platform admin), is audited, and
locks the event row through enqueue so concurrent clicks cannot enqueue twice.
Manual resolution is available to clinic operators but requires a bounded reason. Its
optional note is free text, encrypted at rest, and never copied into logs or
audit metadata; it is returned only through the tenant-scoped route, never the
platform-admin projection. SMS task capture resolves `institution_id` from the sending
location before insert; this is required for the tenant RLS policy to admit the
row at all.

Workflow timer failures are keyed by the timer/run payload fingerprint. Repeated
captures for the same timer update the existing open row instead of creating a
new alert; manual replay/resolution applies to matching open duplicates; and a
later successful dispatch marks earlier open failures for that timer resolved
automatically. Dispatch-time PMS revalidation runs inside a nested transaction
so a failed lookup cannot poison the transaction used by the following
compliance-gate queries.

### 6.4 DNC patients and channel opt-outs

Institution admins manage opt-outs from the **DNC Patients** page
(`/institution-admin/do-not-contact`). Its API projection groups three durable
sources by contact/identity: active `SmsSuppression` rows for SMS STOP replies,
latest revoked `ConsentRecord` rows for voice/email (and legacy SMS consent
revocations), and legacy/staff `DoNotContact` rows that block all channels. The
page shows an independently removable SMS, voice, or email tag plus its location
when the restriction is location-scoped. Removing a tag uses its opaque row ID,
is audited, writes the corresponding granted consent state, and leaves every
other channel restriction intact. Legacy all-channel rows appear as a single
**All channels** tag and are released as one unit.

Spoken Retell opt-outs now write a location-scoped revoked **voice** consent;
they do not suppress SMS or email. Email unsubscribe/bounce/complaint handling
writes an email-identity revocation. SMS STOP continues to create a
location-scoped SMS suppression and now retains an unambiguous matched
`contact_id` so the patient can be named on the DNC page.

Manual campaign enrollment resolves the selected contact and checks active
all-channel `DoNotContact` rows by both contact ID and phone hash before creating
a workflow run. A matching institution- or campaign-location-scoped DNC returns
HTTP 409 with a clear instruction to remove the restriction; the dashboard shows
that detail instead of reporting a successful enrollment. Dispatch-time channel
gates remain authoritative for opt-outs recorded after enrollment and other
send-time consent changes.

### 6.5 Gotchas

- **Env var is misspelled `TWILLIO_` (double-L)**: `TWILLIO_SID`,
  `TWILLIO_API_SECRET` — but `TWILIO_SMS_STATUS_CALLBACK_URL` is spelled
  correctly. Easy to trip on.
- `PUBLIC_API_URL` is non-secret deployment configuration and must be the
  externally reachable backend URL for the current environment, with no trailing
  slash. For local testing, set it to the current HTTPS tunnel URL before saving
  the location's number. Staging and production each use their own value and
  should not manage the same Twilio number.
- Twilio credentials resolve **institution → platform**: an institution may store
  encrypted Twilio sub-account SID/token; otherwise outbound sends and webhook
  signature validation fall back to platform `TWILLIO_SID` /
  `TWILLIO_API_SECRET`. Configure both institution account fields together. The
  Super Admin location picker deliberately does not use this fallback: it requires
  institution credentials so numbers cannot be selected from the wrong account.
- `twilio_from_number` is still **per location**. Reusing one sender number on
  multiple active locations makes inbound `To`-number routing ambiguous and
  should be avoided operationally.
- Twilio client is constructed in two places (`twilio.py` and `sms_service.py`) —
  prefer `SmsService`.

---

## 7. External services & configuration reference

All settings live on the `Settings` class in `src/app/config.py`. Secrets can be
injected via Docker secret files using the `*_FILE` variants.

| Service | Role | Key env vars |
|---|---|---|
| **PostgreSQL (RDS)** | Primary store, RLS multi-tenant | `DATABASE_URL` or `DATABASE_HOST/PORT/NAME/USER/PASSWORD`; pool sizing vars; `DATABASE_ADMIN_URL` for cross-tenant jobs |
| **Redis (ElastiCache)** | Celery broker, sessions, rate limits, NexHealth token cache, SSE pub/sub | `CELERY_BROKER_URL`, `REDIS_URL`, `REDIS_SSL_CERT_REQS` |
| **NexHealth** (PMS) | Universal PMS integration layer for NexHealth-backed institutions | `NEXHEALTH_API_KEY`, `NEXHEALTH_BASE_URL` (`https://nexhealth.info`), `NEXHEALTH_API_VERSION`, `NEXHEALTH_WEBHOOK_SECRET`, connection-pool vars |
| **GoTracker Synchronizer** (PMS) | PMS adapter + webhooks for GoTracker-backed institutions | `GOTRACKER_BASE_URL`, `GOTRACKER_WEBHOOK_SECRET`; per-location product key/base URL fields |
| **Retell AI** (voice) | Inbound and workflow-driven voice agent calls | `RETELL_API_SECRET` (signature verify + read-only agents API) |
| **Twilio** (SMS) | Outbound/inbound SMS, delivery callbacks | `PUBLIC_API_URL`; `TWILLIO_SID`, `TWILLIO_API_SECRET`; optional `TWILIO_SMS_STATUS_CALLBACK_URL` override *(note spelling)* |
| **Resend** (email) | Transactional email — **verified, see below** | `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `RESEND_REPLY_TO`, `RESEND_ALERT_RECIPIENTS` |
| **AWS S3** | Call-recording storage | `AWS_S3_BUCKET_NAME`, `AWS_REGION` (`ca-central-1`) |
| **JWT / Auth** | Access/refresh token signing | `JWT_SECRET` (required), `JWT_ALGORITHM` (HS256), `JWT_ISSUER`, `JWT_AUDIENCE` |
| **Encryption (PHI)** | AES-256-GCM for PHI columns | `ENCRYPTION_KEY` (must differ from `JWT_SECRET` in prod) |
| **WebAuthn / MFA** | Passkeys + TOTP | `WEBAUTHN_RP_ID`, `WEBAUTHN_RP_NAME`, `WEBAUTHN_ALLOWED_ORIGINS` |

### 7.1 Email provider — **verified: Resend (raw HTTP, not SDK)**

Email is sent through **Resend** over its REST API (`POST https://api.resend.com/emails`)
using `httpx` with a bearer token — **no SMTP, no `resend` SDK package**. Two senders:

- `auth_email_service.py` — invites + password resets (never logs the response
  body, because the action URL carries a `?token=` credential).
- `email_notification_service.py` — call-alert/summary emails, using DB-backed
  templates and an `Idempotency-Key` header.

Templates are stored in Postgres (`email_templates` table, `EmailTemplate` model),
rendered with **Jinja2**, managed via `EmailTemplateService` and the
`/api/.../email-templates` routes, with in-code defaults as fallback.

### 7.2 Dependency note

Only **Retell** (`retell-sdk`) and **Twilio** (`twilio`) use a vendor SDK.
NexHealth, GoTracker, and Resend are plain `httpx` HTTP calls. S3 uses `boto3`.

---

## 8. Background work & service modules

### Celery tasks (`src/app/tasks/`)

| Module | Does |
|---|---|
| `notifications.py` | Email notification tasks (call alerts/summaries via Resend) |
| `in_app_notifications.py` | In-app (dashboard) notification tasks |
| `sms.py` | Outbound SMS send tasks (Twilio), auto-SMS enqueue |
| `recordings.py` | Download Retell recordings → upload to S3 |
| `webhooks.py` | Async processing of inbound webhook payloads (post-call pipeline) |
| `automation_workflow.py` | Workflow timers, appointment-triggered enrollment, channel dispatch, GoTracker writeback follow-up |

Recurring jobs (dashboard rollup, audit-partition pre-creation, idempotency/
dead-letter pruning) run as **EventBridge-triggered ECS tasks**, not Celery beat —
see [SCHEDULED_JOBS.md](SCHEDULED_JOBS.md).

### Notable services (`src/app/services/`)

`post_call_service` (post-call pipeline) · `institution_service` (tenant CRUD +
the `retell_agent_id` location lookup) · `sync_service` (pull providers/appt-types/
operatories from the PMS adapter) · `slot_filter` (trim slots to operating hours) ·
`automation/*` (workflow definition, enrollment, scheduling, channel dispatch,
appointment projection, campaign analytics, GoTracker writeback) ·
`sms_service` / `sms_compliance` / `sms_privacy` (SMS send + multichannel consent/DNC) ·
`email_notification_service` / `auth_email_service` / `email_template_service`
(Resend + templates) · `mfa` (WebAuthn/TOTP/recovery) ·
`refresh_token_service` (Redis sessions) · `event_bus` (SSE over Redis) ·
`dead_letter` (capture + replay) · `retention_policy` (PHI windows) ·
`dashboard_rollup` (daily metrics) · `audit` / `audit_decorator` (append-only audit).

---

## 9. Conventions & gotchas for contributors

- **Graph-first.** `graphify-out/graph.json` exists; query the Graphify graph
  (MCP `query_graph`, or `graphify query "..."`) before grepping. Run
  `graphify update .` after editing code. See [../CLAUDE.md](../CLAUDE.md).
- **RLS is the top invariant.** Every PHI table is `institution_id`-scoped with a
  Postgres policy; the runtime DB role is `NOBYPASSRLS`. A new tenant table that
  forgets RLS is logged CRITICAL on startup and caught by the `rls` pytest tier.
  Cross-tenant work uses `DATABASE_ADMIN_URL`, never the app role.
- **`location_id` is mandatory** on PMS routes — never invent a default.
- **PMS routing is adapter-based.** Check `institution.pms_type` before assuming
  NexHealth. `nexhealth`, `gotracker`, and `none` have different setup and
  failure modes, but all PMS-touching routes still require explicit location
  scope.
- **Provisioning is manual** for Retell agents and purchasing Twilio numbers.
  Super Admin stores each institution's Twilio credentials and assigns an owned
  number per location, but no API write path purchases a number or creates a
  Retell agent. For "why isn't this clinic's agent/SMS working", check
  `retell_agent_id`, institution Twilio credentials, and `twilio_from_number`.
- **Schemas the LLM sees live in Retell**, not the repo. Changing a function's
  parameters means editing the Retell dashboard tool config *and* the handler.
- **Vendor credential scope differs**: NexHealth currently uses the platform key
  plus per-location subdomain/location ID; Twilio uses one account per institution
  plus a sender number per location; Retell remains shared; and GoTracker uses
  per-location synchronizer product-key config.
- **`TWILLIO_` env vars are misspelled** (double-L) — match the existing spelling.
- **Migrations** are manual (one-off ECS task before deploy); **CI/CD is manual**
  (no GitHub Actions). For roadmap details, verify the focused roadmap docs and
  current code before relying on older "in-flight" notes.
- **PHI never travels on the SSE channel** — events are payload-free hints;
  clients refetch through the authenticated API.
