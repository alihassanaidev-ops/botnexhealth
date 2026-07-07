# Outbound Engagement Engine — Implementation Plan

Implementation plan for the [Outbound Engagement Engine milestone](./ROADMAP_OUTBOUND_ENGAGEMENT.md). GoTracker is a **separate repo** and out of scope here; this repo only keeps the PMS-agnostic adapter seam stable.

Built to **scale from day one** — but "scale from day one" means *getting the irreversible things right now*, not over-building. We commit to one strategy (Postgres + Celery + Redis) and scale it the way large Django/Postgres shops do: data-layer strategy, not framework swaps.

---

## 1. Decisions locked

| Decision | Choice | Rationale |
|---|---|---|
| **Durable orchestration** | **Postgres state machine** (source of truth) + **Celery** execution + **DB poller** (`FOR UPDATE SKIP LOCKED`) | Durable across restarts, PHI never leaves our RLS DB, no new vendor/BAA, reuses prod stack. Temporal is the documented graduation path (adopt only if workflows become deeply branching/dynamic). |
| **Job queue** | **Celery + Redis** (keep — already in prod, autoscaled) | Scale is strategy, not tool (cf. Instagram on Django+Postgres). Not migrating to ARQ — that's churn, not scale. |
| **Database** | **PostgreSQL (RDS)**, RLS multi-tenancy, partitioning for hot tables | Existing foundation; scales via partitioning + sharding + read replicas. |
| **Voice** | **Retell outbound API** over **Twilio** numbers | Reuse Retell (AI) + Twilio (telephony), both already integrated. |
| **SMS** | **Twilio** + 10DLC/A2P | Already integrated. |
| **Email** | **Resend** now → **SES** if volume grows | Resend fine for moderate; SES cheaper + AWS-native at scale. |
| **Reads** | Normalized **`appointment_cache`**, fed by NexHealth **webhooks** + sync | No per-patient API hammering; cache is the PMS-agnostic read layer. |
| **Writes** | **PMS adapter** (live) only — confirmation status, bookings | Adapter is the single PMS-specific seam (where GoTracker plugs in later). |

## 2. "Scale from day one" = lock the irreversible, defer the reversible

Same principle we used for the infra load test: spend effort where it's **hard to change later**; design-for-but-defer the rest.

| Lock NOW (hard to change) | Defer (easy to scale online later) |
|---|---|
| Data model + **tenant shard key** on every table | Worker count / Fargate size (autoscaled) |
| **Partitioning** scheme for hot tables (`step_attempts`, events) | RDS instance size (online resize) |
| **Idempotency key** shape on every dispatch | Read replicas (add when read load grows) |
| Poller **claim semantics** (`SKIP LOCKED`, lease, visibility timeout) | Caching tiers (Redis read-through) |
| Domain **event schema** (for dashboard + audit) | Resend → SES migration |
| **PMS-agnostic seam** (reads=cache, writes=adapter) | Queue topology tuning (prefetch, priorities) |

## 3. Why Celery + Postgres + Redis scales (the strategy)

The levers — none require changing the stack:
1. **Queue separation** — per-channel + per-priority queues (`voice`, `sms`, `email`, `high`), autoscaled on depth (already in prod).
2. **DB poller with `FOR UPDATE SKIP LOCKED`** — lock-free concurrent claim of "due" steps; N pollers/workers scale horizontally with zero contention.
3. **Partition hot tables** by month/tenant (we already do this for `audit_logs`).
4. **Shard the poller by tenant hash** — fairness (no one clinic starves others) + parallelism.
5. **Idempotency keys + unique constraints** — safe retries at any concurrency; the anti-double-contact guarantee.
6. **Redis token-bucket rate limits** — per vendor (Twilio/Retell/NexHealth) + per clinic (extend the existing NexHealth limiter).
7. **Read replicas + read-through cache** — added online when read load justifies it, not before.

## 4. Architecture

```
            NexHealth ──webhooks/sync──► appointment_cache (Postgres)
                                              │ reads (eligibility/enrollment)
   ┌──────────────┐   claims due    ┌─────────▼─────────┐  enqueue   ┌────────────────┐
   │  DB Poller   │───SKIP LOCKED──►│  Sequence State   │──────────► │ Celery channel │
   │ (sharded)    │                 │  Machine (PG)     │            │ workers        │
   └──────────────┘                 └─────────┬─────────┘            └───────┬────────┘
                                              │ every dispatch               │ writes
                                     ┌────────▼─────────┐            ┌────────▼────────┐
                                     │  POLICY GATE     │            │  PMS adapter    │──► NexHealth
                                     │ consent+DNC+quiet│            │ (confirm/book)  │
                                     └──────────────────┘            └─────────────────┘
                                              │ domain events
                                     ┌────────▼─────────┐
                                     │ events → SSE     │──► dashboard (real-time)
                                     └──────────────────┘
```

**Sequence state machine** (one row per enrolled patient-campaign): `PENDING_ENROLL → SCHEDULED → DUE → POLICY_CHECK → DISPATCHING → SENT → AWAITING_RESPONSE → {RESPONDED | RETRY | ESCALATE} → {COMPLETED | FAILED | DEAD_LETTER}`. Every transition is timestamped, idempotent, and emits a domain event.

**Exactly-once dispatch:** poller claims a `DUE` step with `SELECT … FOR UPDATE SKIP LOCKED` + a lease/visibility timeout → policy gate → channel worker sends with an **idempotency key** (also passed to Twilio/Retell) → unique constraint on `(sequence_step_id, channel, idempotency_key)` makes a duplicate a no-op.

## 5. Data model (Phase 0)

All tables institution/location-scoped under RLS; PHI encrypted or minimized; `created_at` partition key on hot tables.

- `campaigns`, `campaign_versions` — definition + immutable versions
- `sequence_runs` — one per (patient, campaign, trigger); holds state-machine state
- `sequence_steps` — scheduled steps with `due_at`, `channel`, `status`, `lease_until`
- `step_attempts` *(partitioned)* — every send attempt + vendor response + idempotency key
- `outcomes` — terminal results per run (confirmed, booked, opted-out, …)
- `outbound_leads` — sales-qualification leads
- `appointment_cache` — PMS appointment snapshot + eligibility flags (webhook-fed)
- `consent_records`, `do_not_call_lists`, `quiet_hours_overrides`
- `campaign_metrics` — rollups (cf. `call_metrics_daily`)
- `campaign_events` *(partitioned)* — domain event stream for dashboard + audit

## 6. Phases

> Effort is indicative; each phase ends with the acceptance criteria below being demonstrably true in staging.

### Phase 0 — Foundations (~3–4 wks)
Data model + migrations (RLS + partitioning); `appointment_cache` + NexHealth webhook ingestion; reusable **policy gate** (consent + DNC + quiet-hours); idempotency + dead-letter framework; state-machine + sharded poller skeleton; per-vendor/per-clinic rate limiters.
**Done when:** a fake "step" can be scheduled, claimed exactly-once by competing pollers, pass/fail the policy gate, and land in dead-letter on repeated failure — all under RLS, with events emitted. Concurrency/duplicate tests pass.

### Phase 1 — Thin vertical slice: Appointment Reminder over SMS (~2–3 wks)
Enroll from cache → schedule → policy gate → Twilio send (idempotent) → outcome → dashboard event.
**Done when:** a seeded clinic's upcoming appointments generate reminders end-to-end in staging, with zero duplicates under forced retries, quiet-hours respected, opt-out honored, every send audited.

### Phase 2 — All channels + Confirmation (~4–5 wks)
Outbound voice (Retell) + email (Resend); Confirmation campaign (reply → adapter write-back of status); multi-step cross-channel sequences (retry/escalate); Campaign Config Portal.
**Done when:** Confirmation runs across call/SMS/email with channel fallback; a confirmed patient writes back to NexHealth via the adapter; admins configure timing/channels/copy/quiet-hours/retries in the portal.

### Phase 3 — Recall + Sales Qualification + NexHealth expansion (~5–6 wks)
The 6 NexHealth families (recalls/recall-types/procedures/treatment-plans/financials/insurance) behind PHI allowlists + RBAC + audit; Overdue Recall eligibility; Sales Qualification (lead → qualify → book via adapter); Progress dashboard + Analytics.
**Done when:** recall correctly targets overdue patients with no future appointment using recall/treatment-plan data; a qualified lead books via the adapter; analytics show confirmations/reminders/recalls-booked/leads-qualified.

### Phase 4 — Hardening + scale (~3–4 wks)
OTel + correlation-ID observability + alerts + runbooks; **load testing** (reuse the throwaway-stack + k6 tooling from the infra milestone); failure-mode tests (vendor down, duplicate dispatch, stale appointment); 10DLC/A2P registration; feature-flagged per-clinic rollout + migrate-before-traffic gating.
**Done when:** a load test sustains target campaign throughput with zero duplicate contacts; failure injection degrades gracefully; rollout is per-clinic flag-gated.

## 7. Security & compliance (every phase)

- **Policy gate is mandatory and safety-critical** — consent + DNC + quiet-hours (patient/location time zone) enforced *before any* dispatch. Tested adversarially (a double-contact is a TCPA violation, not just a bug).
- **PHI minimization** — orchestration/state carries IDs; PHI is hydrated only inside the send step. Logs redact PHI (reuse `sms_privacy`).
- **RLS** on every new table; **audit** every dispatch, write-back, and config change.
- **Signed webhooks** (Retell, Twilio, NexHealth); **BAAs** with all PHI-touching vendors.
- Encryption at rest (existing `ENCRYPTION_KEY`) for PHI fields.

## 8. Observability & testing

- **Structured logs + correlation IDs** across Retell → backend → poller → queue → vendor; OpenTelemetry → CloudWatch.
- **Metrics:** enrollments, dispatches/min per channel, success/failure, dead-letter depth, policy-gate rejections, vendor latency, queue depth.
- **Tests:** unit (state machine, policy gate, mappers); NexHealth-sandbox integration incl. PHI redaction; e2e (enroll→send→outcome); **idempotency/double-send adversarial tests**; load tests via the existing throwaway-stack harness.

## 9. Decision log (ADRs)

- **ADR-001** Postgres state machine + Celery over Temporal — see §1. Revisit if workflows become deeply branching/dynamic or need per-workflow visibility at scale.
- **ADR-002** Keep Celery + Redis; reject ARQ migration — scale is data-layer strategy, not queue library.
- **ADR-003** Cache-driven reads + adapter-only writes — keeps campaigns PMS-agnostic and protects vendor rate limits.
- **ADR-004** Idempotency key + unique constraint as the anti-double-contact invariant — the one guarantee we never compromise.
