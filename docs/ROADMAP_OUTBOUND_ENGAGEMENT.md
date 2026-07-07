# Next Milestone — Outbound Engagement Engine + Expanded NexHealth Integration

**Scope:** the production-grade outbound platform and expanded NexHealth coverage that live in the **ScaleNexus** backend (this repo).

> **Out of scope here:** the **GoTracker Synchronizer** (local SQL agent, cloud sync gateway, write queue/replay, stored-procedure adapter, installer, sync-health UI). GoTracker is delivered in its **own separate repository** and is tracked there — do **not** add GoTracker code or tables to this repo. The only obligation this repo carries toward it is keeping the **PMS-agnostic adapter contract stable** (see [PMS-agnostic seam](#pms-agnostic-seam)).

Starting point: production is live and tuned (see [`PRODUCTION.md`](./PRODUCTION.md)). This milestone builds the outbound engagement layer on top of that foundation.

---

## 1. Goal

Turn the platform from an **inbound** voice-agent foundation into a full **outbound engagement engine** running four template-based campaigns end-to-end — Appointment Confirmation, Appointment Reminder, Overdue Patient Recall, and Sales Qualification — across voice, SMS, and email, with expanded NexHealth data coverage, all engineered to production standards (idempotency, audit, encryption, retries, observability) from day one.

---

## 2. What already exists (foundation — do not rebuild)

| Component | Status |
|---|---|
| FastAPI backend, auth (JWT + mandatory MFA), RLS multi-tenancy, audit logging | ✅ live |
| **Universal PMS adapter contract** (`src/app/pms/`) + **NexHealth adapter** (`src/app/pms/nexhealth/adapter.py`) — patient search/create, providers, appointment types, operatories, slot search, booking, cancel, reschedule, location lookup | ✅ live |
| Retell **inbound** function flow (signature verify, call context, handlers) + webhooks | ✅ live |
| Slot filtering (operating hours, breaks, buffers, age rules, same-day cutoff) | ✅ live |
| Sync service (providers, appointment types, operatories, NexHealth descriptors → cache) | ✅ live |
| Celery worker + channel primitives: `sms_service`, `email_notification_service` (Resend), `sms_consent` model, notification tasks | ✅ partial |
| React dashboard (admin setup, providers, appt types, operatories, calls, callbacks, audit, institution setup), custom fields, workflow statuses, no-PMS mode, institution groups | ✅ live |
| Production AWS infra (ECS Fargate, RDS Multi-AZ + Proxy, Redis, CloudFront) | ✅ live |

---

## 3. What must be built

### 3.1 Data model & DB foundation
New, all institution/location-scoped under RLS; PHI encrypted/minimized; idempotency keys on dispatch records:
- [ ] `campaigns`, `campaign_versions`
- [ ] `sequence_runs`, `sequence_steps`, `step_attempts`, `outcomes`
- [ ] `outbound_leads` (sales qualification)
- [ ] `appointment_cache` (PMS appointment snapshot + eligibility)
- [ ] `consent_records`, `do_not_call_lists`, `quiet_hours_overrides`
- [ ] `campaign_metrics`
- [ ] `dead_letter_events` for failed outbound dispatch (reuse existing dead-letter pattern)

### 3.2 Scheduling & sequence execution engine
- [ ] Distributed scheduler lock
- [ ] Per-campaign enrollment engine
- [ ] Step executor with **exactly-once dispatch** semantics
- [ ] Per-vendor and per-clinic rate limits
- [ ] Quiet-hours enforcement by patient/location **time zone**
- [ ] Retry + dead-letter handling
- [ ] Circuit breakers around external vendors (Retell, SMS, email) and PMS write paths
- [ ] Campaign event stream for real-time dashboard updates

### 3.3 Outbound voice
- [ ] **Outbound call initiation** with the voice provider (new — only inbound exists today)
- [ ] Lifecycle webhook handling; recording/transcript retention policy
- [ ] Per-clinic concurrency limits; busy / no-answer / voicemail handling; transfer behavior
- [ ] Outcome mapping back into the sequence engine

### 3.4 Outbound SMS
- [ ] Template rendering + safe variable substitution
- [ ] Opt-out keyword handling, suppression lists, delivery-status tracking
- [ ] Carrier throughput limits; **10DLC/A2P** registration support

### 3.5 Outbound email
- [ ] Templates, bounce + complaint handling, sender authentication guidance
- [ ] Optional tracking where permitted; sequence-aware suppression after a response on another channel

### 3.6 The four campaigns, end-to-end
All booking/write-back goes through the **PMS-agnostic adapter** (NexHealth path here):
- [ ] **Appointment Confirmation** — triggered N configurable hours before appointment; captures response; updates confirmation status via the adapter.
- [ ] **Appointment Reminder** — independent of confirmation; uses appointment cache + current PMS state so cancelled/rescheduled appts aren't reminded incorrectly.
- [ ] **Overdue Patient Recall** — patients with no visit in a configured period and no future appointment, using NexHealth **patient recalls, recall types, procedures, appointments, treatment-plan context** (not history heuristics alone).
- [ ] **Sales Qualification** — inbound lead → AI qualifies intent → books qualified lead via the adapter.

### 3.7 Expanded NexHealth integration (six new API families)
Each behind **minimum-necessary PHI rules, RBAC, audit, and per-workflow allowlists** before data reaches Retell or the dashboard:
- [ ] **Procedures** — patient history / treatment-oriented follow-up context
- [ ] **Working Hours** — PMS-backed schedule reconciliation
- [ ] **Financials** — adjustments, charges, claims, balances, fee schedules, payments, plans/types (billing-aware, guarded)
- [ ] **Insurance** — plans + coverages (replace local-only list where NexHealth is authoritative)
- [ ] **Patient Communication** — clinical notes, document types, patient documents, alerts, recalls, recall types, treatment plans
- [ ] **NexHealth Operations** — sync statuses, webhook endpoints/subscriptions, onboarding APIs (monitoring, reconciliation, reduced polling)

### 3.8 Compliance & security
- [ ] Expanded HIPAA audit logging for all outbound actions
- [ ] Strict log review to prevent PHI leakage
- [ ] TCPA + CASL adherence; consent capture + opt-out; do-not-call suppression enforced **before** dispatch
- [ ] Signed Retell webhooks; tenant-isolation regression tests

### 3.9 Observability & operational tooling
- [ ] Dashboard metrics for campaigns (confirmations, reminders, recalls booked, leads qualified, attempts, errors)
- [ ] Structured logging with correlation IDs across Retell calls → backend → queue events
- [ ] Alerting for vendor outages, dispatch failures, queue backlog
- [ ] Runbooks for vendor outage and reconciliation

### 3.10 Frontend
- [ ] **Campaign Configuration Portal** — per-campaign activation, timing, channels, copy, quiet hours, retry limits
- [ ] **Sequence Progress View** — filterable active/completed sequences, real-time updates, attempt history
- [ ] **Analytics Tile** — confirmations, reminders, recalls booked, leads qualified, attributed revenue

### 3.11 Real-time events & access control
- [ ] New event types for campaign progress
- [ ] New permissions: change campaign configuration (audited); each privileged action logged

### 3.12 Testing & QA
- [ ] Unit tests for scheduling, campaign, channel, and mapper logic
- [ ] Integration tests against **NexHealth sandbox** incl. the six new families, permission checks, PHI redaction
- [ ] End-to-end: patient lookup, slot search, booking, cancellation, confirmation (NexHealth path)
- [ ] Load testing for campaign scheduling + queue processing

### 3.13 DevOps & infrastructure (cloud-side)
- [ ] Scheduler deployment + queue workers (extend the existing Fargate worker service)
- [ ] Observability wiring; CI/CD migration checks; smoke tests; staging data
- [ ] Apply the migrate-before-traffic gating from [`PRODUCTION.md`](./PRODUCTION.md) deferred-hardening before high-traffic campaign rollout

### 3.14 Documentation
- [ ] Developer: API docs, PMS adapter contract, architecture decision records
- [ ] Clinic admin: campaign setup guide, clinic configuration guide

---

## PMS-agnostic seam

The campaign and Retell flows must depend **only** on the universal `PMSAdapter` contract — never on a specific PMS. That keeps this milestone PMS-neutral and is the exact seam the **GoTracker repo** plugs into later (it will ship its own adapter implementing the same contract). Action for this repo:
- Keep `src/app/pms/` (the contract) stable and well-documented.
- Any new campaign capability that needs PMS data/writes goes through the contract, not directly against NexHealth, so a second PMS can satisfy it without touching campaign code.
- Do **not** add GoTracker tables, sync-gateway endpoints, write-queue/replay logic, or sync-health UI here.

---

## Effort (Phase 1 only — engineering hours)

| Workstream | Estimate | Range |
|---|---|---|
| 1.1 Foundation & Data Model | 150 | 140–160 |
| 1.2 Outbound Engagement Engine | 320 | 295–345 |
| 1.3 Expanded NexHealth Integration | 180 | 165–195 |
| 1.4 Compliance, Observability & Production Hardening | 150 | 140–160 |
| **Total (this milestone)** | **800** | **740–860** |

GoTracker Synchronizer (Phase 2, ~660–740 hrs) is tracked in its **separate repo**, not here.

---

## Out of scope (explicit)

- GoTracker local synchronizer, cloud sync gateway, write queue/replay, reconciliation worker, stored-procedure write model, GoTracker PMS adapter, Windows installer/auto-update, offline-booking queue, sync-health & mapping-review UI, GoTracker SQL credential handling, stored-procedure tests, GoTracker sandbox work. → **separate GoTracker repository.**
- Clinics cannot invent arbitrary campaign types, build free-form branching workflows, or connect arbitrary third-party systems beyond the defined PMS / Retell / SMS / email / dashboard workflows.
