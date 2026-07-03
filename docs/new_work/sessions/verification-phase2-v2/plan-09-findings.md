# Plan 09 — Integration & Data Layer — Verification Findings

Audited: 2026-07-03. Evidence from code, not session docs.

## Scope (plan deliverables)
Thin event-driven NexHealth read model for trigger discovery + live PMS access at action time:
1. Appointment webhook subscription lifecycle (`nexhealth_webhook_subscriptions`)
2. Webhook receiver endpoint + HMAC signature verification
3. Idempotent async webhook processing + event ledger (`nexhealth_webhook_events`)
4. Appointment working-set projection model/table (`appointment_working_set`)
5. Recall eligibility working set (`recall_eligibility_working_set`) + paced recall pull job
6. Initial REST backfill on subscription activation
7. Paced/jittered reconciliation sweep
8. `PmsLiveRevalidationService` — live re-validation at send time (Finding 13)
9. Rate-limit pacing of the shared NexHealth key for backfill/reconciliation
10. Trigger providers wired into workflow engine (appointment offset, recall)

## What actually exists (evidence)

### Webhook receiver + signature verification — DONE (partial)
- `src/app/api/routes/nexhealth_webhooks.py` (149 lines). Route `POST /api/v1/nexhealth/webhooks/appointments`, registered in `src/app/main.py:38,244`.
- HMAC-SHA256 verification `_verify_signature` (nexhealth_webhooks.py:26-45) using `settings.nexhealth_webhook_secret` (`src/app/config.py:76`, default `""`).
- **Fails OPEN**: when secret is empty, verification is skipped entirely (nexhealth_webhooks.py:32-34). Prod misconfiguration = unauthenticated webhook.
- Only `appointment.created` / `appointment.updated` trigger; `_TRIGGER_EVENTS` (line 23). Cancelled events ignored.
- Resolves location via `InstitutionLocation.nexhealth_location_id`, contact via `Contact.nexhealth_patient_id`, using cross-tenant `get_system_db_session` (lines 90-121). Always returns 200 to avoid NexHealth deactivation.

### Appointment trigger flow — DONE
- `src/app/services/automation/appointment_trigger_service.py` (92 lines): `find_active_appointment_workflows`, `find_active_recall_workflows`, `compute_enrollment_eta`, `make_appointment_idempotency_key` (`appt:{version}:{appt_id}`).
- Celery task `trigger_appointment_workflows` (`src/app/tasks/automation_workflow.py:352-460`) schedules `enroll_and_start_workflow_run` with `eta = appointment_at + offset_hours`. Enrollment idempotency enforced downstream at `enroll()` (tasks:301-306).

### Bulk enrollment — DONE
- `POST /{workflow_id}/bulk-enroll` (`src/app/api/routes/automation_workflows.py:558-616`), max 500 items, 202 async, per-item idempotency key. Not in original plan step list but reasonable adjacency.

### Recall scanner — STUB ONLY
- `scan_recall_workflows` (`tasks/automation_workflow.py:468-527`). Beat-scheduled hourly (`src/app/worker.py:61-64`). Body only counts active `recall_scan` workflows and logs; explicit NOTE (tasks:517-519) that real patient-history query "requires NexHealth sync layer ... later Plan 09 slice." No patient query, no enrollment, no due-date derivation.

## What is MISSING entirely (no code, no migration)
Confirmed absent via `ls src/app/models`, migration grep, and symbol search:
- **`appointment_working_set` model/table** — DOES NOT EXIST. The webhook does NOT persist a projection; it enqueues enrollment directly. The plan's central deliverable (disposable read model) was not built. No trigger reads from a working set.
- **`recall_eligibility_working_set`** — does not exist.
- **`nexhealth_webhook_subscriptions`** — does not exist. No `NexHealthSubscriptionService`, no lifecycle, no health tracking, no re-subscribe.
- **`nexhealth_webhook_events`** — does not exist. No webhook-event ledger, no event-level idempotency claim (unlike Retell/Twilio pattern the plan referenced), no dead-letter, no attempt/retry record. Idempotency exists only at enrollment key, not at event receipt.
- **Initial REST backfill job** — does not exist.
- **Paced reconciliation sweep** — does not exist. No EventBridge/Fargate job.
- **`PmsLiveRevalidationService`** — DOES NOT EXIST (`grep revalidat` → only a security header). No re-validation at send time. Finding 13 mitigation (freshness window, shared per-key budget) is entirely unimplemented.
- **Rate-limit pacing of shared key for backfill/reconciliation** — N/A because those jobs don't exist. `NexHealthRateLimiter` (`src/app/nexhealth/rate_limit.py`) exists for live adapter calls but is unused by any Plan-09 outbound job.
- **Multi-key / tenant-key routing** — not attempted (deferred, was optional).
- **Observability** (stale projection, subscription disabled, reconciliation drift alarms) — not implemented.

## Bugs / implementation gaps
1. **Cancellation not handled — reliability bug.** Plan §Technical Considerations line 153: "cancellations arrive as updates, so status evaluation must happen on every update event." The handler treats every `appointment.updated` as an enroll trigger without inspecting cancelled/rescheduled status (nexhealth_webhooks.py:71-88, 123-136). A cancelled appointment arriving as an update still schedules reminder enrollment. `appointment.cancelled` events are separately ignored (line 23). With no live re-validation at send time either, a cancelled/rescheduled appointment will still be messaged. This is the exact failure the plan called out, unmitigated.
2. **Signature fails open** when `nexhealth_webhook_secret` is unset (nexhealth_webhooks.py:32-34).
3. **No event-level idempotency / dedup.** Duplicate webhook deliveries re-run the full trigger+DB query and re-enqueue tasks; only deduped later at `enroll()`. No record that an event was seen.
4. **Whole-table scan + Python filter**: `find_active_appointment_workflows` / recall load all active workflows and filter `trigger_type` in Python (appointment_trigger_service.py:37-40,53-56; tasks:505-509). Fine at low volume, scales poorly.
5. Recall beat task runs hourly in prod but does nothing useful — dead scheduled work.

## Architectural concerns
- **Core architecture of the plan was not implemented.** The design is an event-sourced disposable projection (working set) that triggers read from, with live re-validation guarding sends. What shipped is a direct webhook→enroll passthrough with no projection and no re-validation. This is a materially different, thinner design and loses the plan's reliability guarantees (out-of-order handling, reconciliation repair, staleness detection, cancellation safety).
- Without backfill, only go-forward appointments created after subscription can ever trigger — the plan explicitly required backfill because webhooks are go-forward-only (plan line 155).
- No subscription lifecycle means webhook wiring is manual/external; no detection of NexHealth deactivating delivery.

## Technical debt
- Recall stub scheduled in beat (worker.py:61) — should be gated/removed until implemented.
- Session docs (`outbound-09-data-layer/task_plan.md`) honestly mark recall/backfill/reconciliation/multi-key as remaining, matching code. But `progress.md` labels Slice 4 webhook "complete for first pass" — accurate only for the passthrough, not the plan's projection design.

## Code quality observations
- Existing code is clean, typed, documented, consistent with repo Celery/session patterns.
- Idempotency key helper and ETA computation are well-factored and tested.
- Webhook handler correctly always-200s and logs; good operational hygiene for the piece that exists.

## Tests
- `tests/unit/test_nexhealth_appointment_webhook.py` — signature verify (skip/missing/wrong/correct), happy path queue, no-contact, ignored cancelled, unknown location, missing start_time, bad JSON.
- `tests/unit/test_automation_plan09.py` — eta computation edge cases, idempotency key, trigger service queries, `_trigger_appointment_async` no-workflows, `_scan_recall_async` summary (asserts stub returns 0/0), `_enroll_and_start_async` duplicate-key skip, bulk-enroll enqueue + inactive-workflow 409.
- **All 23 pass** (`JWT_SECRET=... pytest ... -q` → `23 passed`).
- Coverage gaps: all mock-based, no real DB. No RLS integration test on projection tables (none exist). No backfill idempotency test, no reconciliation test, no rate-limit pacing test, no cancellation-status test, no out-of-order test — because the corresponding features don't exist. Validation Strategy §192-199 items 4-8 are unmet.

## Scope alignment verdict
Roughly 25-30% of the plan delivered. The webhook receiver, signature verification, appointment-offset trigger dispatch, and bulk enroll are real, tested, and wired. But the plan's defining components — the appointment/recall working-set projections, webhook subscription lifecycle, event ledger/idempotency, backfill, reconciliation sweep, recall pull, and live re-validation at send time — are entirely absent, and the recall scanner is an explicit stub. The shipped design is a direct webhook→enroll passthrough, not the disposable read model + live-revalidation architecture the plan specified, and it carries an unmitigated cancellation/reschedule safety gap.
