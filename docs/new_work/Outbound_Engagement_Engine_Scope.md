# Outbound Engagement Engine — Scope of Work

**ScaleNexusAI · Patient Engagement Platform for Dental Clinics**

*Production-grade automated patient engagement across voice, SMS, and email,
orchestrated by a dynamic, multi-tenant, timezone-aware workflow engine with a
visual no-code builder.*

> **Status of this document.** This is a definitive **product scope**, not an
> implementation plan. It defines *what* the Outbound Engagement Engine must do
> and *why*, for both product and engineering, so design and delivery can begin
> without another round of scope discovery. It deliberately avoids code-, schema-,
> and API-level prescription.
>
> It supersedes and expands the high-level vision in
> `Outbound_Engagement_Engine_SOW (1).docx.md`, preserving that intent while
> resolving its ambiguities and anchoring it to the platform that exists today
> (verified against the live codebase).

---

> **Architectural recommendation — tenant-owned PMS integration (future direction).**
> The platform today connects every DSO and clinic location through a **single shared
> NexHealth partner account**, whose rate limit (~1,000 requests/minute) and per-call
> cost are shared across *all* tenants — a bottleneck that grows as the platform scales to
> many organizations and locations (see §3.5). A recommended future direction is a
> **tenant-owned integration model**: just as each clinic can operate on its own Twilio
> account, each clinic or DSO could **connect and manage its own NexHealth account and
> credentials** (subject to NexHealth's account/partnership model). API traffic would then
> be **distributed across many accounts** instead of funnelled through one, materially
> reducing rate-limit pressure, lowering shared-account cost concentration, and improving
> isolation between tenants. The platform already anticipates per-tenant PMS credentials,
> so this is an **extension, not a re-architecture**.
>
> *This is not a launch requirement.* For the current scope, the **event-driven read
> model in §3.5** is what keeps the shared account within its limits. This note is
> recorded as the strategic path for **enterprise scale and growth**.

---

## 1. Executive summary

The platform today is an **inbound** AI voice agent for dental clinics, **built on
Retell AI**. Retell answers a clinic's phone; when the agent needs to do something
— look up a patient, find availability, book an appointment — it invokes a
**configured function call** that our backend services, which in turn calls
**NexHealth** (the universal PMS integration layer), which writes into the clinic's
underlying dental PMS. Clinic staff get a dashboard of every call, plus a manual
**Callback Queue** for patients who asked to be called back.

It is **inbound-only and reactive**: it acts when a patient calls.

The **Outbound Engagement Engine** makes the platform **proactive**. Clinics will
reach patients automatically — to confirm appointments, remind them, recall overdue
patients, qualify and book inbound leads, and (optionally) **handle callback
requests with AI instead of front-desk staff** — across **voice, SMS, and email**.

The foundation is a **dynamic, multi-tenant workflow engine** with a **visual,
no-code builder** (conceptually similar to GoHighLevel). Campaigns are not
hard-coded features; they are *configurations* — compositions of triggers,
conditions, waits, and actions — that the engine executes on a schedule, in each
clinic's local timezone, under full healthcare-communication compliance. The four
launch campaigns ship as pre-built, fully configurable workflow templates on top of
this engine.

Critically, the platform is **multi-tenant**: every clinic location already has its
own Retell agent, its own PMS binding, its own messaging identity, its own timezone,
and fully isolated data — and the Outbound Engagement Engine extends that model so
that **each clinic has its own outbound agents, its own workflows and campaigns, its
own configuration and execution context, and its own operational data**, isolated
from every other tenant.

Everything is **production-grade from day one** — observability, audit trails,
encryption, idempotency, retries, and operator tooling are built into each component
as it ships. There is no separate demo or hardening phase.

---

## 2. Vision and goals

**Vision:** every clinic keeps its schedule full and its patients cared for, without
staff spending hours on the phone.

**Goals**

1. Reduce no-shows through automated confirmation and reminder outreach.
2. Recover revenue by re-engaging overdue/inactive patients.
3. Convert more inbound leads by qualifying and booking them automatically.
4. Let clinics **build and manage their own campaigns visually**, with no technical
   knowledge and no engineering involvement.
5. Offer **AI-driven callbacks** as an alternative to manual front-desk follow-up.
6. Maintain strict healthcare and communication compliance (HIPAA/PHIPA, TCPA,
   CASL, 10DLC/A2P) across every outbound interaction, per tenant.

**The four launch campaigns** (every requirement traces back to at least one):

| # | Campaign | Outcome it drives |
|---|---|---|
| 1 | **Appointment Confirmation** | Confirm attendance before a visit; reduce no-shows |
| 2 | **Appointment Reminder** | Timely day-before / day-of reminders |
| 3 | **Overdue Patient Recall** | Re-engage patients with no recent visit and no future appointment |
| 4 | **Sales Qualification** | Qualify inbound leads and book the qualified ones |

---

## 3. Current architecture — the foundation this builds on

This section states how the platform works today, verified against the codebase, so
the outbound scope extends reality rather than assumptions. Subsections 3.1–3.4
describe the platform as it exists now; **3.5 specifies the integration and scalability
architecture the outbound engine adopts** to extend that foundation.

### 3.1 The inbound voice agent is built on Retell AI

The current inbound agent runs on **Retell AI**. The Retell agent is configured with
a set of **function calls** — for patient lookup, availability search, booking,
cancellation, rescheduling, and other patient interactions. The integration chain
for any of these is:

```
   Patient ── phone ──> Retell AI voice agent
                            │  invokes a configured function call (e.g. book_appointment)
                            ▼
                    Our Backend APIs            (verify the call, resolve the tenant, run the function)
                            │  call the PMS integration layer
                            ▼
                    NexHealth APIs              (the universal PMS integration layer)
                            │  writes/reads the underlying system
                            ▼
                    Dental PMS                  (Dentrix, Eaglesoft, Open Dental, …)
```

**Retell AI → Our Backend → NexHealth → Dental PMS.** Every function the agent calls
follows this same path. NexHealth is the **sole** PMS integration layer (a clinic
either uses NexHealth or has "no PMS," in which case the agent only collects call
intelligence and booking functions are disabled). After each call, Retell sends a
signed post-call event; our backend stores an encrypted transcript, summary, and
classification, and fans out staff notifications.

**The outbound agent reuses this exact pattern** (see §7): the same
function-call-into-our-backend-into-NexHealth chain, the same booking flow, the same
safety properties — adding only the ability to *initiate* a call.

### 3.2 The platform is multi-tenant — each clinic location is its own execution context

The tenancy hierarchy (verified) is three levels:

```
   Institution Group (DSO — optional, read-only oversight)
        └── Institution (the tenant: a clinic company)
              └── Location (a physical office — the execution context)
```

Each **Location** is a self-contained execution context that already owns:

- its **own Retell voice agent** (the agent identity is the routing key that maps an
  inbound call 1:1 to that location),
- its **own NexHealth binding** (subdomain + location id — the mechanism that isolates
  one clinic's PMS data from another's; the system fails closed if it's missing),
- its **own messaging identity** (the outbound SMS sender number),
- its **own timezone, operating hours, and breaks**,
- its **own synced PMS reference data** (providers, appointment types, operatories),
- and its **own tenant-scoped configuration** (custom fields, call/workflow statuses,
  email templates, notification recipients, transfer numbers).

All tenant data is isolated at the database level by row-level security keyed on the
institution/location, enforced by a database role that **cannot bypass** it.

**This is the single most important architectural constraint for this scope:** the
Outbound Engagement Engine must be **per-tenant by construction**. Every clinic gets
its own outbound agents, its own workflows and campaigns, its own configuration and
execution context, and fully isolated operational data — reusing each location's
existing sending identity, timezone, and isolation boundary. No workflow, enrollment,
or metric is ever visible or executable across tenants.

### 3.3 How callbacks work today (verified)

This is called out precisely because the new vision depends on it.

**Today, callback requests are captured and shown to staff.** When a patient on an
inbound call asks to be called back, the post-call analysis can classify the call as
needing a callback. That classification surfaces the call in a **Callback Queue** in
the dashboard, where front-desk staff see the patient, phone number, call summary,
and a suggested next action, and **follow up manually** (then mark it resolved).

Two refinements of detail:

- The **requested time** ("tomorrow at 2 PM") is *not* stored in a dedicated field by
  default and is *not* shown in the Callback Queue. A clinic *can* capture it today
  via a **configurable custom field** mapped to a value the agent extracts, but that
  appears on the call's detail view, not the queue.
- Handling is **manual**: no automated outbound call/SMS reaches the patient — a human
  works the queue.

**The new vision (the distinction this scope introduces):** a clinic that wants
callbacks handled **automatically by AI** can route captured callback requests into an
**outbound workflow**, where the **future outbound Retell agent** performs the
callback — driven by the workflow engine, not by staff. The existing "callback
requested" signal becomes a **workflow trigger**; whether a callback is handled
manually (today's queue) or automatically (outbound agent) becomes a **clinic
configuration choice**. Where useful, the engine can also capture a preferred
callback time as structured trigger input so the outbound agent honors it.

### 3.4 Reusable foundations vs. what's net-new

**Reuse:** Retell function-call integration and PMS booking flow; multi-tenant model
and per-location config; per-location timezone/hours/breaks; compliant one-off SMS
(consent, opt-out, suppression, do-not-contact, delivery status); transactional
templated email; staff notifications and live dashboard updates; asynchronous task
processing with retries/dead-letter; idempotency on PMS-mutating actions; signed
webhooks; audit logging; PHI encryption/retention; and the dashboard's design system
(see §9).

**Net-new (the build):** outbound call initiation; the workflow/sequence engine
(triggers, conditions, waits, actions, enrollment, scheduling); audience/eligibility
and bulk enrollment; quiet-hours/send-window enforcement (none exists today);
bulk/sequenced SMS and campaign email; the **visual workflow builder UI** and campaign
management/progress/analytics screens (no flow-canvas UI exists today); the
**event-driven NexHealth read model** and **per-tenant messaging infrastructure**
described in §3.5; usage metering; and expanded PMS data coverage (§13).

### 3.5 Integration and scalability architecture

The outbound engine extends the real-time integration above with the deliberate additions
required to operate **proactively** and **at scale** — potentially thousands of clinics
sharing one PMS connection.

**PMS data — real-time at action time, event-driven for discovery.**

- **Action-time data stays real-time.** Whenever the engine actually books or reschedules
  an appointment, looks up a patient, or reads availability, it calls NexHealth **live**,
  exactly as the inbound agent does. Nothing is served from a cache, so the PMS stays the
  single source of truth and slot conflicts are resolved by NexHealth.
- **Trigger discovery is event-driven.** Confirmation and Reminder are *system-initiated*:
  the engine must know which appointments are upcoming *before* anyone is on the line.
  Instead of copying the PMS or repeatedly polling it, the platform subscribes to
  **NexHealth appointment webhooks** (created / updated / cancelled) and maintains a
  **thin, disposable working set** of upcoming appointments — a derived index used only to
  decide whom to enroll, never a system of record. Every confirmation/reminder is
  **re-validated live against NexHealth at send time**, so a cancelled or rescheduled
  appointment is never contacted.
  - *Bootstrapping and repair.* Because webhooks are go-forward only, the working set is
    **seeded by an initial REST backfill** when a clinic is onboarded or its subscription is
    (re)established, then kept honest by a **low-frequency, paced reconciliation sweep** that
    repairs anything a dropped webhook missed. Cancellations arrive as appointment
    **updates** (a status change), not a distinct cancel event, so the processor evaluates
    status on every update. Webhook-endpoint health is monitored: NexHealth deactivates
    delivery after a sustained outage, and recovery is a re-subscribe plus a backfill.
- **Recall eligibility comes from the PMS's own recall data.** "Overdue / due for recall"
  is read from **NexHealth's recall lists** via a **scheduled, paced, off-peak** query per
  clinic — asking the PMS *who is due* rather than scanning a whole patient population —
  held only as a transient eligibility list to drive enrollment.
- **No full patient or appointment database is maintained.** Only the minimal derived
  working sets above are kept; identity and clinical detail are always fetched live at
  action time under minimum-necessary rules.
- **Why this design.** NexHealth enforces a **shared, per-partner-key rate limit** (on the
  order of ~1,000 requests/minute across the entire platform) that cannot be divided per
  clinic, and it bills per API call. An event-driven read model scales with the *rate of
  change*, not clinic-count × poll-frequency — making it both the most scalable and the
  lowest-cost approach, and the integration pattern NexHealth itself recommends (webhooks
  in place of polling). Webhook delivery is signature-verified and processed
  **idempotently**, since the platform — not the PMS — owns de-duplication.

**Per-tenant messaging infrastructure.**

To isolate deliverability, compliance, and throughput per clinic — and because
application-to-person registration is required per business — each clinic operates on its
**own messaging infrastructure**, provisioned at onboarding and stored as **encrypted
per-tenant credentials**:

- **Voice & SMS on a per-clinic Twilio sub-account** (the recommended model for platforms
  serving many businesses), each with its **own A2P 10DLC brand and campaign registration**
  and its **own phone numbers**. One clinic's volume or compliance status cannot affect
  another's, and messaging rate limits are **sharded per clinic** rather than sharing one
  platform bottleneck.
- **Email on a per-clinic sending domain** (per-domain SPF/DKIM/DMARC authentication and
  return path), so each clinic sends under its own domain reputation. Clinics that require
  it may instead supply their own email-sending credentials.
- **Outbound caller identity** is therefore each clinic's own number(s) on its own
  sub-account.
- **Outbound voice telephony shards the same way.** Retell concurrency is a **per-workspace**
  resource, so each clinic (or DSO) runs in its **own Retell workspace** — isolating
  concurrency instead of competing for one shared pool — with **bring-your-own-telephony
  (SIP)** binding that clinic's Twilio sub-account numbers to its workspace. (Retell-provisioned
  numbers dial US destinations only, so **imported/BYO numbers carry Canadian outbound**.)
  This shards the voice bottleneck without requiring a Retell Enterprise contract.
- **Application-to-person registration is region-specific.** In the **US**, A2P **10DLC**
  brand + campaign registration is required per business. **Canada has no 10DLC equivalent**:
  the practical requirement is **Twilio toll-free verification** (which covers Canada), with
  CASL governing consent and content (see §11). A Canada-first clinic can go live in roughly
  one to two weeks via toll-free verification, without the longer US 10DLC campaign-approval
  cycle.

(The shared NexHealth key is the one limit that cannot be sharded this way — which is
precisely why the event-driven read model above matters for PMS data.)

---

## 4. Core concepts and terminology

| Term | Meaning |
|---|---|
| **Channel** | A medium of outreach: **Voice**, **SMS**, or **Email**. |
| **Workflow** | A configurable, multi-step automation: an ordered/branching graph of triggers, conditions, waits, and actions. The unit the engine executes. **Tenant-scoped.** |
| **Campaign** | A workflow (or small set) packaged for a business purpose — e.g. "Appointment Confirmation." The four launch campaigns are pre-built workflow **templates**. |
| **Trigger** | The condition that begins enrollment — a time relative to an appointment, a recurring eligibility scan, an inbound event (incl. callback-requested), or a manual/bulk action. |
| **Action** | A step that *does* something — place an AI call, send SMS/email, write back to the PMS, update a contact, notify staff, branch, wait, or exit. |
| **Condition / Branch** | A decision point that routes a contact based on contact, appointment, consent, or response state. |
| **Wait / Delay** | A pause: fixed delay, until a time/offset, or until an event-or-timeout (e.g. "wait for a reply up to 4 hours"). |
| **Enrollment** | Placing a contact into a workflow (via trigger, manual add, or bulk import). |
| **Sequence Run** | One contact's individual journey through a workflow instance, with its own current step, schedule, and state. |
| **Attempt** | A single delivery on a channel within a run (e.g. "confirmation call, attempt 2"). |
| **Outcome** | The result of an attempt or run (confirmed, no-answer, voicemail, booked, opted-out, completed, failed, …). |
| **Quiet hours / send window** | The timezone-aware band during which outreach is permitted for a location (and patient where known). |

---

## 5. The Workflow Engine (the foundation)

A **dynamic, multi-tenant workflow engine**, conceptually similar to GoHighLevel but
scoped tightly to dental patient engagement. Campaigns are **configurations executed
by the engine**, never code.

### 5.1 Design principles

- **Configuration, not code.** New campaigns and changes are authored as workflow
  configurations. Adding a campaign type must not require a code release.
- **Multi-tenant by construction.** Every workflow, enrollment, run, and metric
  belongs to one institution/location and is isolated accordingly. Tenants never see
  or execute each other's workflows.
- **Timezone-aware by default.** All timing is evaluated in the relevant local
  timezone (see §8).
- **Compliance-first.** Consent, suppression, do-not-contact, and quiet hours are
  enforced by the engine before any send — not the author's responsibility to
  remember (see §11).
- **Idempotent and resumable.** A contact is never double-contacted for the same step;
  runs survive restarts and resume from current state.
- **Observable.** Every enrollment, step, attempt, and outcome is recorded and visible
  in near-real-time.

### 5.2 Authoring and lifecycle

- **Pre-built templates.** The four launch campaigns ship ready-to-use and fully
  configurable. Clinics start from a template, not a blank canvas.
- **Configurability.** Per campaign, per location: on/off, timing rules, channel
  selection and order, message wording per channel (approved merge fields), quiet
  hours/send windows, retry rules and max attempts, branching thresholds where
  exposed.
- **Draft → Publish → Active → Paused.** Drafts are editable and never execute.
  Publishing makes a workflow live. Active workflows can be paused (halting new
  enrollments and optionally in-flight runs) and resumed.
- **Versioning.** Publishing creates a version. In-flight runs continue on the version
  they enrolled under; new enrollments use the latest — patients already in a sequence
  don't change behavior mid-flight.
- **Authoring exposure (design decision).** The engine is fully dynamic. The **initial
  clinic-facing experience is template configuration** (honoring "clinics use
  templates"). **Full free-form visual authoring** (arbitrary trigger/condition/action
  graphs) is delivered as a **platform/operator** capability and a clinic-facing
  roadmap item — the engine and the builder UI are designed to support it from the
  start.

### 5.3 Enrollment

- **Trigger-based** (automatic) — the primary mode (see §6).
- **Manual enrollment** — an operator adds a specific contact.
- **Bulk enrollment** — multi-select contacts or **CSV import**, with validation,
  de-duplication against existing contacts, and a preview before commit.

Enrollment always passes eligibility and compliance gates (reachable contact; not
suppressed/opted-out/do-not-contact; within attempt limits; not already in a
conflicting run) — all within the tenant's boundary.

### 5.4 Execution model

- **Multi-step paths** with sequential steps, branches, and merges.
- **Scheduling and timing** — absolute times, relative delays, or offsets relative to
  an anchor event (e.g. "72 hours before the appointment").
- **Timezone-aware evaluation** of every time-based decision (§8).
- **Cross-channel coordination** — when a patient responds or completes the goal on
  one channel, the engine stops the remaining steps on other channels for that run.
- **Retry and give-up semantics** — per-step retry rules and a max-attempts ceiling,
  after which the run exits with a terminal outcome.
- **Exactly-one-action semantics** — the same step for the same contact is never
  dispatched twice, across restarts or retries.

---

## 6. Triggers and Actions catalog

Classified **Critical** (required for the four launch campaigns), **Recommended**
(materially improves them), **Optional** (future enhancement). Only primitives that
serve the intended campaigns are included.

### 6.1 Triggers

| Trigger | Class | Serves | Description |
|---|---|---|---|
| **Appointment time-offset** | **Critical** | Confirmation, Reminder | Enroll a configurable interval before/after an appointment (e.g. 72h before), driven by the event-fed working set of upcoming appointments (§3.5). |
| **Recurring eligibility scan** | **Critical** | Recall | On a schedule, enroll contacts the PMS flags as due for recall (and/or matching an inactivity definition), sourced from NexHealth recall lists (§3.5). |
| **Inbound lead / new contact** | **Critical** | Sales Qualification | Enroll when a new lead/contact is received. |
| **Manual enrollment** | **Critical** | All | Operator adds a specific contact. |
| **Bulk / CSV enrollment** | **Critical** | Recall, Sales Qualification | Import or multi-select contacts. |
| **Callback-requested / status-change** | **Recommended** | Callbacks, Reschedule | Enroll on a captured callback request (the new AI-callback path), or a PMS state change such as a cancellation prompting reschedule outreach. |
| **Inbound reply / keyword** | **Recommended** | All | React to a patient's SMS reply or spoken response to branch or enroll. |
| **Inbound webhook** | **Optional** | Sales Qualification | An external system (e.g. a web lead form) enrolls a contact. |
| **Date / recall-due anniversary** | **Optional** | Recall | Enroll on a PMS recall-due date or patient milestone. |

### 6.2 Actions

| Action | Class | Serves | Description |
|---|---|---|---|
| **Place AI voice call** | **Critical** | All | Initiate an outbound call with the AI agent (see §7). |
| **Send SMS** | **Critical** | All | Compliant templated text. |
| **Send Email** | **Critical** | All | Branded templated email. |
| **Wait / Delay** | **Critical** | All | Fixed delay, wait-until-offset, or wait-for-response-or-timeout. |
| **Conditional branch** | **Critical** | All | Route on contact / appointment / consent / response state. |
| **PMS write-back** | **Critical** | Confirmation, Sales Qualification | Update appointment confirmation status, or book an appointment, via NexHealth. Guarded and idempotent. |
| **Exit / goal-met** | **Critical** | All | End the run on success or terminal failure; stop other channels. |
| **PMS status re-check** | **Recommended** | Reminder, Confirmation | Re-validate against current PMS state before acting (e.g. skip reminding a now-cancelled appointment). |
| **Update contact / tag** | **Recommended** | All | Record qualification result, response, or disposition. |
| **Notify staff / create task** | **Recommended** | Callback, Sales Qualification | Hand off to the front desk — cleanly subsumes today's manual callback queue as a *configured* action (vs. the automated AI-callback path). |
| **Branch on call outcome** | **Recommended** | Voice steps | Route on busy / no-answer / voicemail / answered / booked. |
| **HTTP request** | **Optional** | Sales Qualification, integrations | Call an external API as a step. |
| **Outbound webhook** | **Optional** | Integrations | Notify an external system of a campaign event. |
| **A/B / split** | **Optional** | Optimization | Randomized path split for message/timing testing. |

### 6.3 Control-flow primitives (waits & conditions)

| Primitive | Class | Description |
|---|---|---|
| **Fixed delay** | **Critical** | Pause N minutes/hours/days (drip). |
| **Wait-until-offset** | **Critical** | Resume at a time relative to an anchor (e.g. appointment start). |
| **Quiet-hours / send-window hold** | **Critical** | Defer a send until the next permitted local window (§8, §11). |
| **Max-attempts guard** | **Critical** | Cap attempts per channel/run; exit when exceeded. |
| **Wait-for-event-or-timeout** | **Critical** | Wait for a patient response up to a deadline, then continue. |
| **Eligibility condition** | **Critical** | Gate enrollment/continuation on contact/appointment criteria. |

---

## 7. The Outbound Voice Agent

The outbound agent **mirrors the existing inbound Retell architecture** and reuses
its integration chain end to end (§3.1). It is **per-tenant**: each clinic location
has its own outbound agent identity and sending context, exactly as it has its own
inbound agent today.

### 7.1 Reuse from the inbound agent

- **Same Retell function-call chain** — *Retell AI → Our Backend → NexHealth → Dental
  PMS* — so the outbound agent can **book and schedule appointments** during a call
  exactly as the inbound agent does.
- **Same booking flow** — availability search → filtering by the location's operating
  hours, breaks, and buffers → write the booking to the PMS via NexHealth.
- **Same safety properties** — tenant/location resolved from the agent identity;
  PMS-mutating actions idempotent (no double-booking on retry); the patient-identity
  gate governs protected information; signed webhooks.
- **Same post-call pipeline** — an encrypted record (transcript, summary, outcome
  tags) is persisted asynchronously with only scrubbed content retained — now also
  feeding the workflow sequence engine.

### 7.2 Outbound-specific capabilities (the new part)

- **Call initiation** — the engine starts an outbound call as a workflow action, using
  the **correct per-location caller identity**.
- **Dial-outcome handling** — busy, no-answer, voicemail (optional message), answered,
  and live-transfer-to-staff, each mapped to a workflow-visible outcome.
- **Per-clinic concurrency limits & live-availability booking** — cap simultaneous
  outbound calls per location to match front-desk capacity and vendor limits. Concurrency is
  sharded by running each clinic (or DSO) in its **own Retell workspace** — each workspace
  carries its own concurrency pool — with the clinic's own Twilio sub-account numbers bound
  via SIP, so no single shared pool throttles every tenant (§3.5). Because
  booking always reads **live NexHealth availability** (never a cache), outreach can
  never overfill the schedule from stale data: the PMS rejects conflicts and the agent
  simply offers another slot.
- **Retry behavior** — re-attempt unreachable patients per the workflow's retry and
  quiet-hours rules, up to the attempt ceiling.
- **Outcome feedback into the engine** — the call result drives the next step (booked
  → exit; no-answer → wait then retry; voicemail → fall back to SMS).
- **Recording & transcript retention** consistent with existing PHI retention policy.

---

## 8. Timezone and quiet hours (core capability)

The platform serves clinics across regions (e.g. Canada and the US). Correct
local-time behavior is a **core requirement**.

- **Authoritative timezone, per tenant.** Each location already has a configured
  timezone. Every campaign timing decision — offsets, delays, scheduled scans, send
  windows — is evaluated in **that location's** local timezone.
- **Quiet hours / permitted send windows.** A new, first-class concept (none exists
  today). Outreach is dispatched only within configured local windows; steps that come
  due outside the window are **held until the next permitted window**, never dropped
  and never sent at a non-compliant hour.
- **Per-patient timezone (consideration).** Where a patient's timezone is known or
  inferable, the platform may honor it for compliance; otherwise the location timezone
  governs. **Recommended** refinement, not a launch blocker.
- **Operating hours, breaks, buffers** continue to govern bookable slots and may also
  inform call timing.
- **DST correctness** across daylight-saving transitions.

---

## 9. User experience and interfaces

This feature is **not just backend**. It requires dedicated, intuitive UI delivered in
the existing dashboard (Vite + React + a shadcn/Tailwind design system, role-gated
routing, tenant/location switching, and a live-update channel already exist and are
reused). The flagship surface is a **visual, no-code Workflow Builder**.

### 9.1 Visual Workflow Builder (flagship)

A GoHighLevel-style visual canvas where a non-technical clinic admin builds and manages
campaigns without writing anything:

- **Canvas-based editing.** Workflows are shown as a visual graph of connected steps;
  users see the whole journey at a glance.
- **Side-panel palette.** Triggers and actions are listed in a side panel and **added
  to the canvas** (drag-and-drop or click-to-add) from the catalog in §6.
- **Per-step configuration.** Selecting a step opens a configuration panel to set its
  options (timing, channel, message wording with approved merge fields, branch
  conditions, retry limits) through guided form controls — never raw config.
- **Branching and waits are visual** — conditional paths and delays are first-class
  visual elements, not hidden settings.
- **Validation and guardrails.** The builder prevents invalid or non-compliant
  workflows (e.g. missing quiet hours, a send with no consent path) and explains why.
- **Draft / Publish / Pause** controls with **version history**, surfaced in the UI.
- **Template start.** Users launch from a pre-built campaign template and customize it
  visually, or (for operators / roadmap) start from blank.
- **Preview & test.** Preview rendered messages and test-run a workflow against a
  sample contact before publishing.

> A node-graph/flow-canvas capability and drag-and-drop interaction are **net-new** to
> the frontend (no such UI exists today) and are the primary new UI investment; the
> per-step configuration panels follow existing form/dialog conventions.

### 9.2 Campaign management

- A **campaign list** per tenant/location: each campaign's status (draft/active/paused),
  channels, enrollment count, and key outcomes at a glance; activate/pause/duplicate.
- **Per-campaign configuration** screens for clinics that prefer configuring a template
  over editing the canvas (timing, channels, copy, quiet hours, retries).
- **Enrollment UI** — manually add a contact, multi-select from contacts, or **import a
  CSV** with a validation/preview step.
- **Message/template editing** per channel with live preview and approved merge fields.

### 9.3 Real-time progress dashboard

- A **sequence progress view**: filterable list of active and completed patient
  sequences with **real-time updates**, current step, attempt history, and outcomes.
- Drill-down into a single contact's run (timeline of steps, attempts, responses).
- Operator visibility into queued work, errors, and **dead-letter records with replay
  controls**.
- Live updates delivered over the existing real-time channel (extended with new
  campaign/workflow event types), scoped to the tenant.

### 9.4 Analytics & reporting

- **Campaign analytics**: confirmations, reminders delivered, recalls booked, leads
  qualified, and **attributed revenue**, with trends over time (charting building
  blocks already exist in the dashboard).
- Per-channel delivery and response metrics; comparison across campaigns and locations
  within a tenant.
- **Usage & cost reporting**: voice/SMS/email consumption (Retell minutes and dials,
  Twilio segments and minutes) aggregated **per location, per institution, and across a
  DSO group**, for visibility into engagement volume and spend (see §12).

### 9.5 Cross-cutting UX requirements

- **Tenant/location context** is respected throughout — an admin acts within the
  selected location; location-scoped users are pinned to theirs.
- **Role-gated** access to campaign management and configuration (§11), consistent with
  the existing role model and step-up prompts for sensitive actions.
- **Accessibility, responsiveness, empty/loading/error states, and clear confirmations**
  for destructive actions (pause, delete, bulk enroll), consistent with the current
  design system.

---

## 10. The four campaigns, end to end

Each is a configurable workflow template assembled from §6 primitives, executed
per-tenant in the location's timezone. Descriptions are behavioral (product-level).

### 10.1 Appointment Confirmation
- **Goal:** confirm attendance; reduce no-shows.
- **Trigger:** appointment time-offset (configurable hours before the visit).
- **Eligibility:** appointment still active and not already confirmed.
- **Flow:** reach the patient on configured channel(s); capture confirm /
  reschedule-request / decline / no-response; on confirm, **write the confirmation
  status back to the PMS**; on reschedule-request, branch to rescheduling or hand off;
  retry per rules within quiet hours; suppress remaining channels once answered.

### 10.2 Appointment Reminder
- **Goal:** timely day-before / day-of reminders.
- **Trigger:** appointment time-offset (independent of confirmation).
- **Eligibility:** **re-validated live against current PMS state at send time** (§3.5)
  so cancelled/rescheduled appointments aren't reminded incorrectly.
- **Flow:** send reminder(s) across configured channels; optionally allow confirm or
  change requests; suppress on response.

### 10.3 Overdue Patient Recall
- **Goal:** re-engage patients with no recent visit and no upcoming appointment.
- **Trigger:** recurring eligibility scan (configurable inactivity period) + manual/CSV
  enrollment for targeted lists.
- **Eligibility:** sourced from the PMS's own recall lists (who is due) on a paced,
  scheduled query (§3.5), rather than visit-history heuristics alone; optionally enriched
  with deeper PMS recall context (see §13).
- **Flow:** multi-touch drip across channels with waits between attempts; the AI call
  can **book** when the patient is reached; exit on booking or attempt ceiling.

### 10.4 Sales Qualification
- **Goal:** qualify inbound leads and book the qualified ones.
- **Trigger:** inbound lead / new-contact event; manual and CSV enrollment; inbound
  webhook optional.
- **Flow:** AI call qualifies intent; branch on qualified / not-qualified / unreachable;
  **book** qualified leads onto the right calendar; route hot/ambiguous leads to staff;
  record the qualification result on the contact.

---

## 11. Compliance, security, and access control

Compliance applies to **every** outbound interaction and is enforced by the engine
before dispatch, per tenant.

- **PHI protection (HIPAA / PHIPA):** encryption at rest for all PHI, including any
  queue or cache that holds it; minimum-necessary exposure — financial, clinical, and
  document data gated behind per-workflow allowlists, role-based access, and audit
  logging before use in outreach or display.
- **Communication compliance (TCPA / CASL):** consent capture and proof; immediate
  opt-out honoring; **do-not-contact suppression enforced before every dispatch**;
  **quiet-hours enforcement per location (and patient where known) timezone**; and
  **per-clinic A2P 10DLC brand and campaign registration**, since each clinic sends on
  its own Twilio sub-account (§3.5). This registration is **region-specific**: the US
  requires A2P 10DLC; **Canada has no 10DLC regime**, where the equivalent is **Twilio
  toll-free verification plus CASL** — express/implied consent, clear sender identification
  in every message, and a STOP/unsubscribe honored within the statutory window, with
  bilingual (EN/FR) keyword handling.
- **Cross-channel & cross-campaign suppression:** an opt-out or met goal suppresses
  further contact appropriately across channels and conflicting campaigns. Opt-out /
  do-not-contact is scoped **per location** — each location sends under its own number and
  brand, so a STOP applies to that sender — and suppresses **all** outbound channels for that
  location; a privileged staff action can additionally set an **institution- or DSO-wide**
  do-not-contact for patients who ask to be removed everywhere. Suppression and consent
  records carry both the **channel** and the **location** they apply to.
- **Audit:** every patient lookup, booking, cancellation, confirmation, status change,
  and **campaign-configuration change** is audit-logged with privileged actions
  attributed to an actor.
- **Access control (RBAC):** managing campaigns and viewing engagement data are
  governed by roles. New permissions cover creating/editing/publishing/pausing
  workflows and configuring campaigns, restricted to appropriate administrative roles
  and **tenant/location scoped**. (Extends the existing role model — platform operator,
  institution admin, location admin, staff, and read-only group oversight.)
- **Tenant isolation:** engagement data is strictly tenant/location isolated at the
  database level (the same row-level isolation the platform already enforces), with
  regression tests guarding cross-tenant leakage.

---

## 12. Reliability, observability, and operations

- **Idempotent dispatch** so retries/restarts never double-contact a patient for the
  same step.
- **Retries with dead-letter routing** for items needing manual review, with operator
  **replay** controls.
- **Circuit breakers** around the PMS, voice, SMS, and email vendors so a vendor outage
  degrades gracefully.
- **Distributed scheduling** that runs due work reliably across workers without
  duplicating it.
- **Rate-limit management** — cluster-wide pacing of the **shared NexHealth partner key**
  (the binding PMS constraint, mitigated by the event-driven read model in §3.5), plus
  per-clinic limits on the sharded Twilio sub-accounts.
- **Usage metering** — capture per-interaction consumption from Retell (minutes, dials)
  and Twilio (segments, minutes), tagged by location/institution, and aggregate it up the
  location → institution → DSO hierarchy for reporting and optional budget controls.
- **Structured logging with correlation IDs** spanning the voice call, backend
  processing, and queue/sequence events.
- **Metrics and alerting** for campaign progress, delivery outcomes, stale processing,
  repeated vendor failures, failed replays, and queue backlog.
- **Runbooks** for vendor outage and operational recovery.

---

## 13. Expanded NexHealth coverage (references / future considerations)

The deeper PMS data families below **enrich** the campaigns (especially Recall and
billing-aware context) but are **not the primary focus**. They are references and
phased considerations; the engagement engine, workflow builder, sequences, and
campaigns take priority. Each is gated by minimum-necessary rules, RBAC, audit
logging, and per-workflow allowlists before any financial, clinical, or document data
reaches the voice agent or the dashboard.

| NexHealth data family | Engagement use (when adopted) |
|---|---|
| **Procedures / visit history** | Richer recall and treatment-oriented follow-up |
| **Working hours** | PMS-backed schedule reconciliation where PMS is authoritative |
| **Insurance** | Authoritative insurance plans/coverage in place of local-only lists |
| **Recalls / recall types / treatment plans / alerts / documents** | Safer, better-targeted recall enrollment and patient context |
| **Financials** | Billing-aware dashboards and guarded billing workflows |
| **Sync status / webhooks / onboarding** | Reconciliation, monitoring, and reduced polling (push instead of pull) |

Sequence these after the core engine and campaigns are live.

---

## 14. Deliverables

| Deliverable | What it means |
|---|---|
| **Workflow engine** | Dynamic, multi-tenant, timezone-aware engine executing triggers/conditions/waits/actions with enrollment, scheduling, retries, and exactly-one dispatch |
| **Visual Workflow Builder UI** | GoHighLevel-style no-code canvas: side-panel palette, drag/add steps, per-step config, branching/waits, draft/publish/version, preview/test |
| **Outbound voice calling** | Per-tenant AI outbound calls with booking, dial-outcome handling, concurrency, and outcome feedback |
| **Outbound SMS** | Sequenced/bulk compliant texting with opt-out, suppression, delivery tracking, quiet hours, 10DLC/A2P |
| **Outbound email** | Sequenced branded email with bounce/complaint handling and cross-channel suppression |
| **Four live campaigns** | Confirmation, Reminder, Overdue Recall, Sales Qualification — end to end as configurable templates |
| **AI callback handling** | Optional automation of captured callback requests via the outbound agent + workflow engine |
| **Campaign management + progress + analytics UI** | Per-tenant campaign list, enrollment (incl. CSV), real-time sequence progress, and reporting (incl. attributed revenue) |
| **Integration & data layer** | Real-time PMS access at action time plus an event-driven NexHealth read model (appointment webhooks + recall lists) for trigger discovery, with idempotent webhook processing |
| **Per-tenant messaging provisioning** | Per-clinic Twilio sub-accounts (with A2P 10DLC brand/campaign) and per-clinic email sending domains, with encrypted credential storage |
| **Usage & cost reporting** | Retell + Twilio consumption metered and aggregated per location / institution / DSO |
| **Compliance framework** | HIPAA/PHIPA, TCPA/CASL, 10DLC/A2P, audit, encryption, minimum-necessary PHI |
| **Operations tooling** | Operator queue/dead-letter views with replay, metrics, alerting, runbooks |
| **Documentation** | Developer documentation and a clinic-admin configuration guide |

---

## 15. What clinics can and cannot do (launch boundaries)

**Can:** turn each campaign on/off per location; set timing, channels, wording
(approved merge fields), quiet hours, retries, attempt caps; manually and bulk/CSV
enroll contacts; choose manual vs. AI callback handling; build/customize campaigns
visually within provided primitives; monitor progress and outcomes.

**Cannot (at launch):** invent arbitrary campaign types outside the provided
primitives; connect arbitrary third-party systems beyond the defined PMS, voice, SMS,
email, and dashboard integrations; act across tenants. (Full free-form authoring is a
roadmap consideration; the engine and builder are designed to support it.)

---

## 16. Assumptions and open questions

- **Authoring exposure.** Launch exposes template configuration + guided visual
  customization to clinics; full free-form authoring stays operator/roadmap. *Confirm
  this is the intended launch boundary.*
- **Callback automation default.** Confirm the default posture per clinic (manual queue
  vs. AI outbound) and whether to capture a preferred callback time as structured input.
- **Patient-level timezone & consent for voice/email.** Per-patient timezone and
  email/voice consent models are recommended refinements; confirm whether any are
  launch-blocking for the target regions.
- **Attributed revenue.** Confirm the definition/source of "attributed revenue."

---

*Primary focus, restated: the Outbound Engagement Engine — a multi-tenant, dynamic
workflow builder with a visual no-code UI, sequences, campaigns, and outbound
voice/SMS/email executed per clinic in its own timezone under full compliance, reusing
the existing Retell AI → Our Backend → NexHealth → Dental PMS architecture. NexHealth
data-coverage expansion is a supporting, phased consideration.*
