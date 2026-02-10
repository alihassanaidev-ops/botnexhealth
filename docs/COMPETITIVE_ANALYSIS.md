# Competitive Analysis: Us vs Dentina

> Source: Sales call recording between our team member (Zulkaif) and Dentina sales rep (Mason).
> Date: February 2026

---

## Table of Contents

1. [What Dentina Sells](#1-what-dentina-sells)
2. [What They Actually Do (Under the Hood)](#2-what-they-actually-do-under-the-hood)
3. [Our Architecture vs Theirs](#3-our-architecture-vs-theirs)
4. [Feature-by-Feature Comparison](#4-feature-by-feature-comparison)
5. [What NexHealth Handles (Not Them, Not Us)](#5-what-nexhealth-handles-not-them-not-us)
6. [Gaps We Should Close](#6-gaps-we-should-close)
7. [Where We Are Already Ahead](#7-where-we-are-already-ahead)
8. [NexHealth APIs: What We Use vs What Exists](#8-nexhealth-apis-what-we-use-vs-what-exists)
9. [Actionable Roadmap](#9-actionable-roadmap)

---

## 1. What Dentina Sells

From the sales call, Dentina positions itself as an AI dental receptionist that:

- **Answers phone calls** using an AI voice agent
- **Schedules appointments** directly into the PMS via NexHealth
- **Handles new patients, existing patients, emergencies, cleanings**
- **Routes complex requests** (crowns, root canals) to manual team review or blanket consultations
- **Multi-location support** with aggregated analytics dashboard
- **Custom configuration** per clinic (appointment types, providers, age rules, insurance rules)

### Their Pricing (CAD, from call)
- Large clinic (5 doctors, 8-9 hygienists): ~$999/mo
- Smaller clinic (2 doctors, 3 hygienists): ~$749/mo
- Annual: 20% off ($799 and $599)
- Multi-location: additional ~10% off
- 30-day free trial, up to 3 locations
- Unlimited calls/minutes, no overage fees
- No setup fees, no contract

### Their Onboarding
- ~1 hour per clinic
- Fill out configuration forms in advance
- 15-minute PMS integration setup (NexHealth link)
- They configure everything on their backend

---

## 2. What They Actually Do (Under the Hood)

Dentina uses **NexHealth as their synchronizer** (Mason confirmed this on the call). Their architecture:

```
AI Voice Agent (phone + text)
        |
Configuration Layer (forms-based rules)
        |
NexHealth Synchronizer
        |
PMS (Open Dental / Dentrix / Eaglesoft / Curve)
```

### Key Revelation: Their AI Does NOT Make Scheduling Decisions

The PMS + NexHealth provide ALL scheduling constraints:
- Available slots (NexHealth `/appointment_slots` API)
- Operatory conflicts (NexHealth handles automatically)
- Double booking prevention (NexHealth removes booked slots)
- Provider availability windows (NexHealth `/availabilities` API)
- Buffer times between appointments (PMS configuration)

Dentina's AI only:
1. Collects patient information (name, DOB, phone)
2. Selects from allowed appointment types
3. Calls NexHealth booking API

### Their "Configuration" Is Just Forms

All the rules Mason described (allowed appointment types, age limits, insurance requirements, provider restrictions) are collected via onboarding forms and baked into the AI agent's prompt/rules. This is NOT automated intelligence -- it's manual config.

---

## 3. Our Architecture vs Theirs

### Our Stack
```
Retell AI Voice Agent
        |
PMS-Agnostic Adapter Layer (PMSAdapter interface)
        |
    +---+---+
    |       |
NexHealth  Sikka
    |       |
   PMS     PMS
```

### Key Files in Our System
| Component | File | Purpose |
|-----------|------|---------|
| PMS Interface | `src/app/pms/base.py` | Abstract `PMSAdapter` with 12 methods |
| NexHealth Adapter | `src/app/pms/nexhealth/adapter.py` | NexHealth-specific implementation |
| Sikka Adapter | `src/app/pms/sikka/adapter.py` | Sikka-specific implementation |
| Universal Models | `src/app/pms/models.py` | PMS-agnostic data models |
| Adapter Factory | `src/app/pms/factory.py` | Cached adapter creation per tenant/location |
| Retell Handlers | `src/app/retell/handlers.py` | 13 AI functions (PMS-agnostic) |
| Tenant Resolution | `src/app/retell/functions.py` | Resolves tenant from Retell agent_id |
| Sync Service | `src/app/services/sync_service.py` | Caches providers/appointment types locally |
| Audit Log | `src/app/models/audit_log.py` | HIPAA-compliant audit trail |
| Webhooks | `src/app/retell/webhooks.py` | Call tracking + GHL CRM sync |
| Tenant Model | `src/app/models/tenant.py` | Multi-tenant with AES-256-GCM encryption |
| Location Model | `src/app/models/tenant_location.py` | Multi-location with credential inheritance |

### Our AI Agent Functions (13 total)
| Function | What It Does |
|----------|-------------|
| `lookup_patient` | Search by name/email/phone/DOB |
| `create_patient` | Create new patient record |
| `find_appointment_slots` | Get available time slots |
| `book_appointment` | Create appointment |
| `cancel_appointment` | Cancel existing appointment |
| `reschedule_appointment` | Cancel + rebook atomically |
| `list_appointment_types` | Get appointment types for practice |
| `list_providers` | Get doctors/hygienists |
| `list_operatories` | Get rooms/chairs |
| `list_locations` | Get practice locations |
| `get_location_details` | Get address/hours/info |
| `create_appointment_type` | Create new type (NexHealth) |
| `link_availability` | Link provider to type/operatory/schedule |

---

## 4. Feature-by-Feature Comparison

| Feature | Dentina | Us | Who Wins |
|---------|---------|-----|----------|
| AI voice agent | Yes (own agent) | Yes (Retell AI) | Tie |
| NexHealth integration | Yes | Yes | Tie |
| PMS support | NexHealth-only (OD, Dentrix, Eaglesoft, Curve) | NexHealth + Sikka (extensible) | **Us** |
| Multi-location dashboard | Yes | Yes (admin dashboard built) | Tie |
| Multi-tenant SaaS | Yes | Yes (AES-256-GCM encrypted) | Tie |
| Patient matching | Name + DOB + phone | Name + email + phone + DOB | Tie |
| Duplicate patient prevention | Yes (claimed) | Yes (search before create) | Tie |
| Operatory awareness | Claims it, but NexHealth does it | NexHealth does it | Tie (both NexHealth) |
| Double booking prevention | Claims it, but NexHealth does it | NexHealth does it | Tie (both NexHealth) |
| Buffer times | Claims it, but PMS config | PMS config | Tie (both PMS) |
| Appointment type guardrails | Yes (forms-based config) | **Not yet** | **Dentina** |
| Age/insurance rules | Yes (forms-based config) | **Not yet** | **Dentina** |
| Complex request routing | Yes (consult or team notify) | **Not yet** | **Dentina** |
| CRM integration | Unknown | Yes (GoHighLevel) | **Us** |
| HIPAA audit logging | Unknown | Yes (append-only audit_logs) | **Us** |
| PMS data caching | Unknown | Yes (TenantProvider, TenantAppointmentType) | **Us** |
| Availability linking API | Unknown | Yes (create/list availabilities) | **Us** |
| Appointment type creation API | Unknown | Yes (via NexHealth) | **Us** |
| Analytics dashboard | Yes (claimed) | **Not yet** | **Dentina** |
| Patient recalls | Not mentioned | **Not yet** | Neither |
| NexHealth webhook subscriptions | Not mentioned | **Not yet** | Neither |

---

## 5. What NexHealth Handles (Not Them, Not Us)

This is critical. Many features both companies claim are actually NexHealth doing the work:

### Operatory Constraints
> "Operatories are treated as the **limiting resource** when it comes to calculating availability. Providers can be booked in more than one operatory at the same time, but operatories **cannot** have more than one appointment booked in them at the same time."
> -- NexHealth Docs

The `/appointment_slots` API automatically:
- Factors in operatory constraints
- Removes booked slots (prevents double booking)
- Deduplicates across operatories (same slot in multiple ops = 1 result)
- Respects provider availability windows
- Returns slots in location timezone

### What This Means
When Mason said "It won't double book" and "it understands operatory constraints" -- that's NexHealth, not Dentina. We get this for free too.

### Provider Schedule Handling
NexHealth Availabilities API manages:
- Provider working hours (per day, recurring or one-time)
- Appointment type restrictions per provider
- Operatory assignments per provider
- Active/inactive toggle

Both synced-from-PMS and manually-created availabilities are supported.

### Buffer Times
Buffer times between appointments are configured in the PMS itself (Open Dental, Dentrix, etc). NexHealth reads them from the PMS. Neither Dentina nor we configure this -- the office does it in their existing software.

---

## 6. Gaps We Should Close

### Gap 1: Scheduling Guardrails / Rules Engine (HIGH PRIORITY)

**What Dentina has**: Per-clinic configuration defining which appointment types the AI is allowed to book, per provider. "Go ahead and schedule cleanings and new patient exams. Everything else, have the team do it."

**What we have**: Nothing. Our `list_appointment_types` handler returns ALL types from NexHealth. The AI can attempt to book anything.

**Suggested solution**: Add a `TenantSchedulingConfig` model or extend `TenantAppointmentType` with an `ai_bookable` flag. Filter in `list_appointment_types` and `find_appointment_slots` handlers before returning to the AI.

**Minimal schema**:
```python
# On TenantAppointmentType (already exists)
ai_bookable: bool = True  # Default allow, admin can restrict
fallback_action: str = "consultation"  # "consultation" | "notify_team" | "transfer"
```

### Gap 2: Complex Request Routing (MEDIUM PRIORITY)

**What Dentina has**: When a patient asks for a crown/root canal:
- Option A: Book a blanket consultation instead
- Option B: Notify team via text/email/dashboard and say "Let me have the team reach out"

**What we have**: Nothing. The AI would either try to book it (if the type exists) or say it can't help.

**Suggested solution**: Use the `fallback_action` field above. In the agent prompt, instruct the AI to check if the requested type is `ai_bookable=false`, and then follow the configured fallback.

### Gap 3: Analytics Dashboard (LOW PRIORITY for now)

**What Dentina has**: Multi-location analytics, business performance overview.

**What we have**: Raw audit logs in PostgreSQL. All the data is there (every booking, cancellation, call), just no aggregation or frontend.

**Suggested solution**: Build aggregate API endpoints on top of `audit_logs` table. We already log everything with `AuditAction` enums and `tenant_id`.

---

## 7. Where We Are Already Ahead

### 1. PMS-Agnostic Architecture
Dentina is locked to NexHealth. We have a clean `PMSAdapter` interface with NexHealth + Sikka, and adding a new PMS is just implementing the abstract class. This is a major architectural advantage.

### 2. Universal API Layer
Our `/api/universal/` routes and Retell handlers work identically regardless of PMS backend. Same `BookingRequest`, same `BookingResult`, same `UniversalSlot`.

### 3. GoHighLevel CRM Integration
Dentina didn't mention CRM integration. We sync call data (summary, recording, transcript, patient info) to GHL automatically via Retell webhooks. This gives clinics a CRM pipeline for every call.

### 4. HIPAA-Compliant Audit Trail
Append-only `audit_logs` table with: timestamp, actor, action, target_resource, outcome, tenant_id. No PHI logged. 23 distinct audit actions tracked. Dentina didn't mention HIPAA compliance.

### 5. Availability Linking API
We expose `link_availability()` and `create_appointment_type()` -- meaning we can programmatically set up a clinic's NexHealth scheduling without them needing to touch NexHealth directly. Dentina does this manually in their onboarding.

### 6. Multi-Adapter Caching
Adapter factory with `tenant_id:location_id` keyed cache + explicit invalidation. Token management with auto-refresh. Connection reuse across calls.

---

## 8. NexHealth APIs: What We Use vs What Exists

### APIs We Use (Complete for Current Flow)
| API | Endpoint | Our Usage |
|-----|----------|-----------|
| Patients | `GET /patients`, `POST /patients` | Search + create |
| Providers | `GET /providers` | List with availabilities + types |
| Operatories | `GET /operatories` | List rooms/chairs |
| Appointment Types | `GET /appointment_types`, `POST /appointment_types` | List + create |
| Appointment Slots | `GET /appointment_slots` | Query availability |
| Appointments | `POST /appointments`, `PATCH /appointments/{id}` | Book + cancel |
| Availabilities | `GET /availabilities`, `POST /availabilities` | List + link |
| Descriptors | `GET /locations/{id}/appointment_descriptors` | List EHR procedure codes |
| Institutions | `GET /institutions` | List locations |
| Locations | `GET /locations/{id}` | Get location detail |
| Auth | `POST /authenticates` | Token management |

### APIs We Should Add

#### Patient Recalls (HIGH VALUE)
- `GET /patient_recalls` + `GET /recall_types`
- Tells us which patients are overdue for hygiene/checkups
- AI agent could say "I see you're due for a cleaning" during calls
- Enables outbound recall campaigns
- **Dentina does NOT mention this -- real differentiator**

#### Sync Status (OPERATIONAL NEED)
- `GET /sync_status`
- Tells us if PMS-to-NexHealth sync is healthy
- Currently if NexHealth's sync breaks, our cached data silently goes stale
- Should check before trusting slot data

#### NexHealth Webhooks (STAY IN SYNC)
- `POST /webhook_endpoints` + `POST /webhook_subscriptions`
- Subscribe to appointment changes (created, updated, cancelled by front desk)
- Keeps our audit log in sync when office makes changes outside the AI
- Currently we only handle Retell webhooks, not NexHealth webhooks

### APIs We Don't Need (For Now)
| API | Why Not Now |
|-----|-----------|
| Insurance Plans / Coverages | Future feature (insurance-based guardrails) |
| Procedures | We use appointment types + descriptors instead |
| Patient Documents | Not relevant to voice scheduling |
| Patient Alerts | Not relevant to voice scheduling |
| Charges / Payments / Adjustments | Financial data, not scheduling |

---

## 9. Actionable Roadmap

### Phase 1: Close the Gaps (Weeks)

#### 1A. Scheduling Guardrails
- Add `ai_bookable` (bool) and `fallback_action` (enum) to `TenantAppointmentType`
- Filter in `list_appointment_types` handler: only return `ai_bookable=True` types to the AI
- Update agent prompt to check if requested service is bookable
- Admin dashboard: toggle which types the AI can book per location
- **Effort**: ~2-3 days backend + 1 day frontend

#### 1B. Complex Request Routing
- Implement `fallback_action` logic in agent prompt:
  - `consultation`: "Let me book you a consultation with the doctor instead"
  - `notify_team`: "Let me have the team reach out to you about that"
  - `transfer`: "Let me transfer you to someone who can help with that"
- Add notification mechanism (webhook/email to clinic when `notify_team` triggered)
- **Effort**: ~1-2 days (mostly prompt engineering + notification endpoint)

#### 1C. Auto-Sync Scheduler
- Background task to re-sync providers/appointment types every 24h
- We already have `SyncService.sync_location()` -- just need a scheduler
- **Effort**: ~half day

### Phase 2: Differentiate (Weeks to Month)

#### 2A. Patient Recalls Integration
- Add `GET /patient_recalls` to NexHealth adapter
- New Retell handler: `check_patient_recalls`
- AI can proactively mention: "By the way, I see you're due for a cleaning"
- **Effort**: ~2 days

#### 2B. NexHealth Webhook Subscriptions
- Subscribe to `appointment.*` events
- Update audit log when office modifies appointments outside AI
- Keep `TenantAppointmentType` / `TenantProvider` in sync automatically
- **Effort**: ~3 days

#### 2C. Analytics API
- Aggregate endpoints on `audit_logs`:
  - Calls per day/week/month per location
  - Booking success rate
  - Most common appointment types
  - Average call duration (from Retell webhook data)
- Frontend dashboard cards
- **Effort**: ~3-4 days

### Phase 3: Beat Them (Month+)

#### 3A. Auto Clinic Modeling
- On first sync, analyze PMS appointment types and suggest guardrail config
- "We found 12 appointment types. We recommend allowing AI to book: New Patient Exam, Hygiene Cleaning, Emergency. Restrict: Crown Prep, Root Canal, Extraction."
- Pre-fill config forms instead of making clinics fill them from scratch
- **Effort**: ~1 week

#### 3B. Smart Consultation Routing
- Instead of blanket "consultation" fallback, use intent + history:
  - "Patient mentioned crown" -> "Dr. X diagnosed crown on 2024-12-01" -> book crown follow-up
  - "Patient mentioned pain" -> route to emergency slot
  - "New patient, unknown need" -> route to new patient exam
- **Effort**: ~1-2 weeks

#### 3C. Fuzzy Patient Matching
- Confidence scoring for patient search results
- Handle: shared family phones, spelling variants, twins
- "Found 3 possible matches. Confirming with patient..."
- **Effort**: ~3-4 days

---

## Key Takeaways

1. **Dentina's product is mostly NexHealth + forms + prompt engineering.** Their scheduling "intelligence" is NexHealth doing the heavy lifting. Their config is manual forms.

2. **Our architecture is stronger.** PMS-agnostic adapter pattern, multi-PMS support (NexHealth + Sikka), HIPAA audit logging, CRM integration, availability linking API.

3. **Our main gap is the business rules layer.** We need `ai_bookable` flags on appointment types and fallback routing for complex requests. This is ~3-4 days of work to close.

4. **Our biggest differentiation opportunity is patient recalls and auto clinic modeling.** Dentina doesn't do either. Recalls let the AI proactively sell hygiene visits. Auto modeling removes the manual onboarding burden.

5. **For the current voice agent flow, we already use every NexHealth API we need.** The three worth adding are: Patient Recalls, Sync Status, and NexHealth Webhooks.
