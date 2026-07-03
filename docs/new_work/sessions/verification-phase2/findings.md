# Findings — Phase 2 Verification

## Claim map (from session records)
- 01 engine: mostly complete, sends stubbed. 06 templates: complete (def layer). 08 UI: partial.
  09 data: partial (webhook+bulk done, recall/reactivation stub). 02 builder: NOT started.
  local-dev: infra. 03/04/05/07/10/11/12: no session.

## Plan 01 — Workflow Engine (VERIFIED, agent A)
**Real & faithful:** immutable versioning (definition_service.py:116, new version row, unique constraint),
durable timers w/ SKIP LOCKED claim (scheduler_service.py:83, claim_token/expires), guarded run state
machine (runtime_service.py), dispatcher wait/send/condition/exit (step_dispatcher.py), compliance gate
Protocol seam + NoOp (compliance_gate.py:28/42), full lifecycle API (automation_workflows.py), RLS+FORCE
on all 6 tables + RBAC (migration 20260702_auto_workflow_core.py:291), 13 test files.

**By-design stubs:** send nodes (step_dispatcher.py:217 stub_dispatched), NoOp gate, recall scan (tasks.py:473 log only).

**GENUINE GAPS (not by design):**
1. **Emergency halt MISSING** — plan requires halt-all-runs distinct from pause; only per-run cancel_run exists.
2. **recover_stale_claims defined (scheduler_service.py:124) but NEVER scheduled** — no beat entry → crashed
   worker leaves timer stuck CLAIMED forever. Breaks the durable-scheduler crash guarantee. MOST MATERIAL.
3. **Enrollment idempotency not concurrency-safe** — dedup grain mismatch (service dedups on
   (institution_id, idem_key) but unique index is (institution_id, workflow_version_id, idem_key));
   SELECT-then-INSERT w/ no IntegrityError catch → concurrent same-key enroll raises unhandled unique violation.
4. **Inline enroll hardcodes location_timezone="UTC"** (route:309) — contradicts plan's location-tz authority;
   Celery paths do it right (tasks:174).
5. Missing named components: WorkflowValidationService/ActionRegistry/TriggerRegistry/QuietHoursService
   (validation folded into Pydantic; quiet-hours flag never enforced). blocked/BLOCKED status = dead state.
   DST spring-forward nonexistent-time not handled (step_dispatcher.py:271).

**Deviations (documented open decisions, not silent):** create-and-publish instead of draft-first; PATCH
re-publishes new active version inline.

**Verdict:** Engine largely implemented & faithful to non-sending scope; NOT complete per spec (halt, stale-claim
recovery scheduling, idempotency race, inline-enroll tz are real gaps).

## Plan 09 — Data Layer (VERIFIED, agent B)
**Real & correct (the 4 claimed slices):** webhook receiver (nexhealth_webhooks.py:48, POST /api/v1/nexhealth/
webhooks/appointments, always 200), HMAC-SHA256 constant-time sig verify (:26, compare_digest), location+contact
resolution tenant-scoped (:90-121), AppointmentTriggerService + correct ETA math (appointment_trigger_service.py:59),
appointment-trigger Celery task w/ eta + stable idem key (automation_workflow.py:357), bulk enroll ≤500 via schema
max_length (automation_workflows.py:403/389). Enroll-level dedup is the real idempotency (created flag).

**Stub honesty: GOOD.** Recall scan genuinely a no-op stub that logs (automation_workflow.py:473, runs hourly but
enrolls nothing). Reactivation genuinely absent (only a template exists, no scanner). No overstatement within claimed slices.

**GENUINE GAPS / concerns:**
1. **SECURITY: signature bypass by default** — empty nexhealth_webhook_secret (config.py:76 default "") silently
   SKIPS all verification (nexhealth_webhooks.py:32-34). Unauthenticated POST can enqueue enrollments in prod if unset.
   No prod startup guard.
2. **No webhook-edge idempotency** — no nexhealth_webhook_events claim table (Retell has one). Duplicate/replayed
   appointment.updated re-enqueues every time; saved only by downstream enroll dedup. No payload audit/replay protection.
3. **MultipleResultsFound → 500 → retry storm** — location lookup uses scalar_one_or_none on nexhealth_location_id only
   (ignores subdomain, :98); two locations sharing an id → uncaught 500.
4. **Cancellation not handled** — cancellations arrive as appointment.updated; webhook ignores appt status, never
   skips/cancels already-scheduled enrollments. PmsLiveRevalidationService (planned) absent.
5. **Most of Plan 09's documented scope UNBUILT** — no projection/read-model tables (appointment_working_set,
   recall_eligibility_working_set), no subscription lifecycle, reconciliation, backfill, live revalidation. Session
   records honest about THEIR scope, but plan scope >> implemented.

**Scalability:** bulk enroll = up to 500 apply_async round-trips in-request (not batched); trigger task loads all
active appointment workflows and Python-filters (linear per event). Fine now, not at scale.

## Plan 08 UI + Plan 02 (VERIFIED, agent D)
**Plan 08 real & API-aligned:** Campaigns.tsx (list, StatusBadge, TriggerLabel, pause/resume/archive per-row,
status-gated), CampaignDetail.tsx (detail, enrollment/runs table, empty states), app-sidebar.tsx:107 nav
(institutionAdminNav only), router.tsx:269 RoleGuard allowed=INSTITUTION_ADMIN. automation-api.ts 6 endpoints all
map to real backend routes — NO dead calls, NO mismatches. Types match backend response models.

**Correctly absent (matches "partial"):** analytics/charts, CSV/manual enroll UI (backend enroll routes exist but
UI never calls them), run-detail timeline, operations/emergency-halt, usage/cost, SSE live update (manual refresh only),
plan's sub-routes (/overview /enroll /runs /analytics) not present (flat routes).

**Concerns:** RBAC is CLIENT-SIDE redirect only (RoleGuard.tsx:16 redirects, doesn't block) — real enforcement must
be backend (Plan 01 routes use get_current_institution_user per agent A, so backend IS enforced). Archive uses native
confirm() not styled Dialog. Errors → generic toast only.

**Plan 02 builder: CONFIRMED NOT STARTED** — no builder/canvas route, no react-flow/xyflow/dagre in package.json,
no workflow-authoring API client. Backend PATCH/publish exist but no UI consumes them. Placeholder folder only. HONEST.

## Plan 06 — Campaign Templates (VERIFIED, agent C)
**Real & correct:** 4 templates in campaign_templates.py (appointment-reminder-24h, appointment-confirmation-48h,
recall-sms-6month, reactivation-sms-email-18month) — CampaignTemplate/get_template/list_templates. All 4 VALIDATE
against Plan-01 WorkflowDefinition schema (agent reproduced independently). Routes automation_templates.py list/get/
instantiate w/ RBAC (list/get institution_or_location_admin; instantiate institution_user). Tests exist
(test_automation_campaign_templates.py) parametrized schema validation.

**CONFIRMED BUG (self-verified):** instantiate route BROKEN — automation_templates.py:98 calls create_draft(trigger_type=,
definition=) but definition_service.py:38 create_draft has NO such params & no **kwargs → TypeError every call. Also
trigger_type/definition are read-only derived props (models:145,151) so definition would never persist even without the
crash. Unit test MASKS it via AsyncMock service. "Instantiate complete" claim is OVERSTATED.

**Deviations from Plan 06:** plan's 4 launch campaigns = Confirmation, Reminder, Recall, Sales Qualification. Shipped =
first 3 + Reactivation (a "future extensibility" campaign, not a launch four). Sales Qualification correctly deferred.
Outcome vocabulary simplified, not plan's normalized outcome mapping. Hardcoded English "Reply STOP" copy, no per-tenant
config, no unsubscribe footer on email. No frequency-cap enforcement (NoOp gate).

## SELF-VERIFIED headline findings (not just agent claims)
1. instantiate TypeError — CONFIRMED (read automation_templates.py:98-103 + definition_service.py:38-48).
2. recover_stale_claims never scheduled — CONFIRMED (grep: only defn/docstring/tests reference it).
