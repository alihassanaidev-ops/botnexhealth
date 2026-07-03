# Repository Context — Onboarding & Integration Guide

> Orientation for a new developer or AI agent. This document deliberately
> **does not repeat** what the existing docs already cover well — it fills the
> gaps and ties the pieces together. Read the cross-referenced docs for depth.

## Where to start (existing docs — read these first)

| Read this for | Doc |
|---|---|
| System overview, request lifecycle, RLS, call lifecycle, data model | [ARCHITECTURE.md](ARCHITECTURE.md) |
| NexHealth/PMS: auth, rate limits, slot & booking API, per-PMS caveats | [NEXHEALTH.md](NEXHEALTH.md) |
| Auth, MFA, tenant isolation, PHI encryption, SMS consent, retention | [SECURITY.md](SECURITY.md) |
| HIPAA/PHIPA/PIPEDA readiness (scope, vendors, gaps, policies) | [compliance/](compliance/README.md) |
| Deploy runbook + infra compliance | [DEPLOYMENT_AND_HIPAA_GUIDE.md](DEPLOYMENT_AND_HIPAA_GUIDE.md) |
| Recurring jobs catalog + local debug harness | [SCHEDULED_JOBS.md](SCHEDULED_JOBS.md) |
| CDK infra (ECS Fargate, RDS, etc.) | [../infra/README.md](../infra/README.md) |

**This document adds:** the full multi-tenant hierarchy (incl. the
`InstitutionGroup` oversight tier the other docs miss), the complete RBAC
permission model, how each clinic's voice agent is provisioned, the voice-agent
booking orchestration, the Twilio/phone-number model, and a consolidated
external-services & config reference.

---

## 1. Product in one paragraph

An **AI voice agent for dental clinics**. Retell answers the clinic's phone; our
backend gives the agent function calls into the clinic's practice-management
system (PMS) — patient lookup, slot search, booking, cancel, reschedule — through
**NexHealth as the universal PMS integration layer**. Clinic staff get a web
dashboard with per-call transcripts, summaries, tags, a callback queue, and daily
metrics, plus email/in-app/SMS notifications. The platform is **dental-specific**
and ships under the **ScaleNexus** brand.

Why NexHealth matters: dental PMSs (Dentrix, Eaglesoft, Open Dental, …) each have
their own integration surface. NexHealth normalizes all of them behind one REST
API, so the voice agent speaks one protocol regardless of the clinic's PMS. We
never talk to a PMS directly. See [NEXHEALTH.md](NEXHEALTH.md).

---

## 2. Multi-tenant hierarchy

Three tenancy concepts. The first two are in ARCHITECTURE.md; **`InstitutionGroup`
is not documented elsewhere and is easy to miss.**

```
InstitutionGroup          (e.g. a DSO — read-only oversight tier; optional)
   └── Institution        (one clinic company / tenant root — owns all PHI scope)
         └── InstitutionLocation   (one physical practice/office)
               ├── nexhealth_subdomain + nexhealth_location_id  → PMS binding
               ├── retell_agent_id                              → voice-agent binding
               ├── twilio_from_number                           → outbound SMS identity
               ├── operating hours, breaks                      → slot filtering
               └── transfer numbers (per department)            → live-call transfer
```

- **`Institution`** is the tenant root. Every PHI-bearing row carries
  `institution_id`; Postgres RLS enforces isolation (see ARCHITECTURE.md §Multi-tenancy).
- **`InstitutionLocation`** is one physical office. Slugs are unique **per
  institution**, not globally (`src/app/models/institution_location.py`). Each
  location independently binds to its own NexHealth subdomain/location, its own
  Retell agent, and its own Twilio sender number. For a multi-location institution
  there is **no "default" location** — `location_id` is mandatory on every
  PMS-touching route, because guessing would route a booking into the wrong
  clinic's PMS (`src/app/pms/factory.py`).
- **`InstitutionGroup`** (`src/app/models/institution_group.py`) models a parent
  org (e.g. a **DSO** that owns several clinic companies). It exists **only** to
  power the read-only `GROUP_ADMIN` oversight role across member institutions (see
  §3). It carries no PHI of its own.

**A DSO with multiple clinics** maps to: one `InstitutionGroup` → several
`Institution`s → each with its `InstitutionLocation`s.

---

## 3. RBAC & permission model

> SECURITY.md's authorization section is **stale** — it says "four roles." There
> are **five**, and the fifth (`GROUP_ADMIN`) plus the `ContactLocationAccess`
> visibility mechanism are the substance of this section.

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

### 3.3 "How a location user only sees their patients" — `ContactLocationAccess`

This is the non-obvious part. A location-scoped user does **not** see every
contact in the institution. Visibility is granted per-contact via the
`contact_location_accesses` junction table
(`src/app/models/contact_location_access.py`, unique on `contact_id`+`location_id`):

- Grants are **auto-created on call ingestion**: when a call resolves to a
  location, `post_call_service.py` upserts a `(contact_id, location_id)` grant.
- Contact reads filter through it (`src/app/api/routes/contacts.py`): a
  location-scoped user requesting a contact with no grant row gets **404, not 403**
  (existence is hidden). `INSTITUTION_ADMIN` (no `location_id`) bypasses the filter
  and sees all institution contacts.

### 3.4 Role → route-group matrix (derived from router guards)

| Route group | Allowed roles |
|---|---|
| `/admin/*` (institutions, users, groups, twilio, sms, dead-letter) | **SUPER_ADMIN only** |
| `/group/*` | **GROUP_ADMIN only** |
| `/institution/setup`, `/institution/statuses` | INSTITUTION_ADMIN, LOCATION_ADMIN |
| `/institution/email-templates`, `/custom-fields`, `/notification-recipients`, dashboard mutations | **INSTITUTION_ADMIN only** |
| `/institution/contacts` (writes) | INSTITUTION_ADMIN, LOCATION_ADMIN |
| `/institution/sms` | all 3 institution roles |
| `/institution/*` portal (reads) | all 3 institution roles + `require_location_scope()` |

`institution_portal.py` is the mixed-tier file: same prefix, individual routes
step up from "any portal user + location pin" to "institution_admin only"
depending on sensitivity — check the per-route `dependencies=` when editing it.

---

## 4. The voice agent: Retell

### 4.1 How each clinic gets its own agent — **provisioning is MANUAL**

There is **no code in this repo that creates, duplicates, or configures Retell
agents**, imports phone numbers, or defines tool/function JSON schemas. All of
that is done in the **Retell dashboard**. The repo's only outbound Retell API
calls are **read-only**, used by admins to pick/verify an existing agent:

- `GET /api/.../retell/agents` → Retell `list-agents` (`admin_institutions.py`)
- `GET /api/.../retell/agents/{id}` → Retell `get-agent/{id}` — *"verify a manually
  entered Retell Agent ID."*

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
    `cancel_appointment`, `reschedule_appointment`
- **Idempotency** (`src/app/retell/idempotency.py`): unique on
  `(call_id, function_name, HMAC(args))`. A Retell retry replays the cached result
  instead of double-booking; in-flight duplicates get a retryable "still
  processing" response.
- **Identity gate:** `lookup_patient` requires DOB **plus** exact email or
  phone-last-4 before any PHI is read back.

After the call, Retell posts a `call_analyzed` webhook → the request thread only
verifies the signature, claims an idempotency row, and enqueues a Celery task
(<100 ms response). The worker runs the post-call pipeline. Full lifecycle is in
ARCHITECTURE.md §Call lifecycle.

### 4.4 Appointment booking flow (voice-agent orchestration)

What the agent's tool calls do, in order, during a booking call — this is the
voice view that stitches the Retell functions to NexHealth (PMS-side details in
[NEXHEALTH.md](NEXHEALTH.md)):

1. **Identify the caller** — `lookup_patient` (DOB + email/phone-last-4 gate).
   If not found and the caller wants to book → `create_patient`.
2. **Scope the request** — `list_appointment_types`, `list_providers`,
   `list_operatories`, `list_insurance_plans` give the LLM the location's
   bookable options (these are a local cache of PMS reference data, synced on
   demand by `sync_service.py`).
3. **Offer times** — `find_appointment_slots` queries NexHealth availability,
   then `slot_filter.py` trims results to the location's operating hours/breaks
   before the agent reads them out.
4. **Book** — `book_appointment` (idempotency-wrapped) writes the booking back
   into the PMS via NexHealth. `cancel_appointment` / `reschedule_appointment`
   handle changes.
5. **Transfer if needed** — `list_transfer_numbers` returns the location's
   per-department numbers so Retell can transfer the live call (the bridging
   happens on Retell's telephony side, not here).

All of these execute under the location resolved from `agent_id` (§4.2), so a
booking can only ever land in that location's PMS.

---

## 5. Twilio & phone numbers

Twilio in this codebase is an **SMS integration only**. The inbound **voice**
number → Retell routing is configured externally (Retell/Twilio dashboards); the
repo has no Twilio Voice/TwiML voice wiring.

### 5.1 The three phone fields

- `InstitutionLocation.twilio_from_number` — the location's **outbound SMS sender**
  (E.164). `SmsService.send_sms` rejects any `from_number` that doesn't exactly
  match it.
- `InstitutionLocation.phone` — the clinic's human contact number (used in SMS
  HELP text).
- `InstitutionLocationTransferNumber` (`institution_location_transfer_numbers`) —
  per-department numbers for **live-call transfer**, surfaced to the agent via the
  `list_transfer_numbers` function.

**Provisioning is manual** — numbers are purchased externally, not via API.
`client.incoming_phone_numbers.list()` (`api/routes/twilio.py`) is read-only, so an
admin can *see* owned numbers; `twilio_from_number` is then set through admin
location CRUD.

### 5.2 Webhooks (`src/app/api/routes/twilio_webhooks.py`, prefix `/twilio/webhooks`)

- `POST /inbound-sms` — keyword opt-out/in (STOP/UNSUBSCRIBE/… → suppress;
  START/UNSTOP → release; HELP/INFO → help text). Routes `To` number → location
  via `twilio_from_number`. Replies with TwiML.
- `POST /sms-status` — delivery-status callback; updates the `SmsHistoryLog`.

Both **require Twilio signature validation** (`RequestValidator` against the raw
URL + form): **503** if the secret isn't configured, **401** on missing/invalid
`X-Twilio-Signature`. Unmatched location / `MessageSid` → dead-letter.

### 5.3 Outbound SMS

Single chokepoint `SmsService.send_sms` (`src/app/services/sms_service.py`):
enforces the sender number, **gates on consent** (`SmsComplianceService` — a
blocked send logs a `SUPPRESSED` row and never calls Twilio), logs a `PENDING`
row (encrypted body, masked/hashed number), calls Twilio (offloaded via
`asyncio.to_thread`), and updates status. Entry points: admin sync
`POST /admin/twilio/send-sms` (audited) and the async `send_sms_message` Celery
task (`tasks/sms.py`, 5 retries, exp backoff, dead-letters on exhaustion).
Call-triggered auto-SMS is enqueued from the post-call pipeline only if a body +
patient phone + `twilio_from_number` are all present.

### 5.4 Gotchas

- **Env var is misspelled `TWILLIO_` (double-L)**: `TWILLIO_SID`,
  `TWILLIO_API_SECRET` — but `TWILIO_SMS_STATUS_CALLBACK_URL` is spelled
  correctly. Easy to trip on.
- **Single platform-level Twilio account** serves all tenants; the auth token
  doubles as the webhook-signature secret. No per-tenant Twilio credentials.
- Twilio client is constructed in two places (`twilio.py` and `sms_service.py`) —
  prefer `SmsService`.

---

## 6. External services & configuration reference

All settings live on the `Settings` class in `src/app/config.py`. Secrets can be
injected via Docker secret files using the `*_FILE` variants.

| Service | Role | Key env vars |
|---|---|---|
| **PostgreSQL (RDS)** | Primary store, RLS multi-tenant | `DATABASE_URL` or `DATABASE_HOST/PORT/NAME/USER/PASSWORD`; pool sizing vars; `DATABASE_ADMIN_URL` for cross-tenant jobs |
| **Redis (ElastiCache)** | Celery broker, sessions, rate limits, NexHealth token cache, SSE pub/sub | `CELERY_BROKER_URL`, `REDIS_URL`, `REDIS_SSL_CERT_REQS` |
| **NexHealth** (PMS) | Universal PMS integration layer | `NEXHEALTH_API_KEY`, `NEXHEALTH_BASE_URL` (`https://nexhealth.info`), `NEXHEALTH_API_VERSION`, connection-pool vars |
| **Retell AI** (voice) | Inbound voice agent | `RETELL_API_SECRET` (signature verify + read-only agents API) |
| **Twilio** (SMS) | Outbound/inbound SMS, delivery callbacks | `TWILLIO_SID`, `TWILLIO_API_SECRET`, `TWILIO_SMS_STATUS_CALLBACK_URL` *(note spelling)* |
| **Resend** (email) | Transactional email — **verified, see below** | `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `RESEND_REPLY_TO`, `RESEND_ALERT_RECIPIENTS` |
| **AWS S3** | Call-recording storage | `AWS_S3_BUCKET_NAME`, `AWS_REGION` (`ca-central-1`) |
| **JWT / Auth** | Access/refresh token signing | `JWT_SECRET` (required), `JWT_ALGORITHM` (HS256), `JWT_ISSUER`, `JWT_AUDIENCE` |
| **Encryption (PHI)** | AES-256-GCM for PHI columns | `ENCRYPTION_KEY` (must differ from `JWT_SECRET` in prod) |
| **WebAuthn / MFA** | Passkeys + TOTP | `WEBAUTHN_RP_ID`, `WEBAUTHN_RP_NAME`, `WEBAUTHN_ALLOWED_ORIGINS` |

### 6.1 Email provider — **verified: Resend (raw HTTP, not SDK)**

Email is sent through **Resend** over its REST API (`POST https://api.resend.com/emails`)
using `httpx` with a bearer token — **no SMTP, no `resend` SDK package**. Two senders:

- `auth_email_service.py` — invites + password resets (never logs the response
  body, because the action URL carries a `?token=` credential).
- `email_notification_service.py` — call-alert/summary emails, using DB-backed
  templates and an `Idempotency-Key` header.

Templates are stored in Postgres (`email_templates` table, `EmailTemplate` model),
rendered with **Jinja2**, managed via `EmailTemplateService` and the
`/api/.../email-templates` routes, with in-code defaults as fallback.

### 6.2 Dependency note

Only **Retell** (`retell-sdk`) and **Twilio** (`twilio`) use a vendor SDK.
NexHealth and Resend are plain `httpx` HTTP calls. S3 uses `boto3`.

---

## 7. Background work & service modules

### Celery tasks (`src/app/tasks/`)

| Module | Does |
|---|---|
| `notifications.py` | Email notification tasks (call alerts/summaries via Resend) |
| `in_app_notifications.py` | In-app (dashboard) notification tasks |
| `sms.py` | Outbound SMS send tasks (Twilio), auto-SMS enqueue |
| `recordings.py` | Download Retell recordings → upload to S3 |
| `webhooks.py` | Async processing of inbound webhook payloads (post-call pipeline) |

Recurring jobs (dashboard rollup, audit-partition pre-creation, idempotency/
dead-letter pruning) run as **EventBridge-triggered ECS tasks**, not Celery beat —
see [SCHEDULED_JOBS.md](SCHEDULED_JOBS.md).

### Notable services (`src/app/services/`)

`post_call_service` (post-call pipeline) · `institution_service` (tenant CRUD +
the `retell_agent_id` location lookup) · `sync_service` (pull providers/appt-types/
operatories from NexHealth) · `slot_filter` (trim slots to operating hours) ·
`sms_service` / `sms_compliance` / `sms_privacy` (SMS send + consent/DNC) ·
`email_notification_service` / `auth_email_service` / `email_template_service`
(Resend + templates) · `mfa` (WebAuthn/TOTP/recovery) ·
`refresh_token_service` (Redis sessions) · `event_bus` (SSE over Redis) ·
`dead_letter` (capture + replay) · `retention_policy` (PHI windows) ·
`dashboard_rollup` (daily metrics) · `audit` / `audit_decorator` (append-only audit).

---

## 8. Conventions & gotchas for contributors

- **Graph-first.** `graphify-out/graph.json` exists; query the Graphify graph
  (MCP `query_graph`, or `graphify query "..."`) before grepping. Run
  `graphify update .` after editing code. See [../CLAUDE.md](../CLAUDE.md).
- **RLS is the top invariant.** Every PHI table is `institution_id`-scoped with a
  Postgres policy; the runtime DB role is `NOBYPASSRLS`. A new tenant table that
  forgets RLS is logged CRITICAL on startup and caught by the `rls` pytest tier.
  Cross-tenant work uses `DATABASE_ADMIN_URL`, never the app role.
- **`location_id` is mandatory** on PMS routes — never invent a default.
- **Provisioning is manual** for both Retell agents and Twilio numbers today.
  `retell_agent_id` / `twilio_from_number` are set via admin CRUD; no API write
  path creates them. This is the most common source of "why isn't this clinic's
  agent/SMS working" — check those two fields are populated.
- **Schemas the LLM sees live in Retell**, not the repo. Changing a function's
  parameters means editing the Retell dashboard tool config *and* the handler.
- **Single shared vendor accounts**: one NexHealth key (per-location isolation via
  subdomain + location_id), one Twilio account, one Retell account. Per-clinic
  credential columns exist (`nexhealth_api_key_encrypted`) but are not wired up.
- **`TWILLIO_` env vars are misspelled** (double-L) — match the existing spelling.
- **Migrations** are manual (one-off ECS task before deploy); **CI/CD is manual**
  (no GitHub Actions). See ARCHITECTURE.md §In-flight work for the roadmap and
  known limitations (no-PMS mode, per-clinic NexHealth credentials, etc.).
- **PHI never travels on the SSE channel** — events are payload-free hints;
  clients refetch through the authenticated API.
