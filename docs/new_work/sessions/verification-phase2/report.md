# Phase 2 Implementation Verification Report — Detailed Findings Register
## Outbound Engagement Engine — session records vs. actual codebase

**Date:** 2026-07-03
**Branch:** `feature/outbound-engagement-engine`
**Purpose:** Systematic, finding-by-finding verification of the work documented in
`docs/new_work/sessions/*` against the actual implementation, for sign-off and to drive the
remaining development. Every observation from the analysis is captured below — including minor ones.

---

## How to read this document

Each finding has a stable ID (`AREA-NN`) and the fields you requested: **Title · Description ·
Affected files/components · Why it's a problem · Expected · Current · Impact & severity · Evidence**.

**Severity scale**
- **CRITICAL** — broken/insecure now; blocks correct operation or sign-off of that item.
- **HIGH** — will cause incorrect behavior, data loss, or a security/legal exposure under realistic conditions.
- **MEDIUM** — correctness/robustness/maintainability issue that will bite under load, edge cases, or future work.
- **LOW** — minor correctness, polish, or maintainability nit.
- **INFO** — accurate-as-documented status / by-design note recorded for completeness.
- **DECISION** — a deviation that is a legitimate open product/architecture decision, not a defect.

**Verification tag**
- `[self-verified]` — the reporting author re-read the exact code/line this session.
- `[traced]` — established by a verification pass with a cited `file:line`; not personally re-read.

**Areas:** `ENG` = Plan 01 Engine · `TPL` = Plan 06 Templates · `UI` = Plan 08/02 Frontend ·
`DATA` = Plan 09 Data Layer · `XCUT` = cross-cutting · `SCOPE` = scope/not-started.

---

## Master index

| ID | Title | Severity | Verify |
|---|---|---|---|
| ENG-01 | Durable scheduler never recovers crashed-worker timers (`recover_stale_claims` unscheduled) | CRITICAL | self |
| ENG-02 | Emergency-halt capability missing entirely | HIGH | self |
| ENG-03 | Enrollment idempotency not concurrency-safe (unguarded SELECT-then-INSERT) | HIGH | self |
| ENG-04 | Enrollment dedup grain ≠ DB unique-index grain | MEDIUM | self |
| ENG-05 | Inline enroll path hardcodes `location_timezone="UTC"` | HIGH | traced |
| ENG-06 | `respect_quiet_hours` flag defined but never enforced | MEDIUM | self |
| ENG-07 | `BLOCKED` run/step status is a dead state (never assigned) | LOW | self |
| ENG-08 | DST spring-forward (nonexistent local time) unhandled | MEDIUM | traced |
| ENG-09 | Named engine components from the plan don't exist as modules | LOW | traced |
| ENG-10 | No frequency-cap / blast-radius / spend-cap enforcement | MEDIUM | traced |
| ENG-11 | Create-and-publish instead of draft-first lifecycle | DECISION | traced |
| ENG-12 | PATCH re-publishes a new active version inline | DECISION | traced |
| ENG-13 | `workflow_enrollment_locks` table not created | INFO | traced |
| TPL-01 | Template instantiate endpoint raises `TypeError` (broken) | CRITICAL | self |
| TPL-02 | Even without the crash, template definition would never persist | CRITICAL | self |
| TPL-03 | Instantiate unit test masks the bug via a mocked service | HIGH | traced |
| TPL-04 | Shipped Reactivation instead of the plan's Sales-Qualification launch campaign | MEDIUM | traced |
| TPL-05 | Template outcome vocabulary diverges from the plan's normalized mapping | MEDIUM | traced |
| TPL-06 | Hardcoded English copy; no per-tenant/merge-field/consent config | MEDIUM | traced |
| TPL-07 | Reactivation email template has no unsubscribe / CAN-SPAM footer | HIGH | traced |
| TPL-08 | Templates encode no frequency ceilings; gate is NoOp | MEDIUM | traced |
| TPL-09 | Recall/reactivation legal classification unresolved | MEDIUM | traced |
| TPL-10 | Template key naming diverges from plan (`-24h` vs `_reminder`) | LOW | traced |
| TPL-11 | Template test suite cannot run locally (missing `structlog`) | LOW | traced |
| TPL-12 | `send_voice` channel intentionally omitted from templates | INFO | traced |
| DATA-01 | Webhook signature verification silently disabled when secret unset (default) | CRITICAL | self |
| DATA-02 | No webhook-edge idempotency / replay-protection / payload audit | HIGH | traced |
| DATA-03 | `MultipleResultsFound` on location lookup → uncaught 500 → retry storm | MEDIUM | traced |
| DATA-04 | Appointment cancellation/reschedule not handled | HIGH | traced |
| DATA-05 | Most of Plan 09's data-layer scope (projection/subscription/reconciliation) unbuilt | HIGH | traced |
| DATA-06 | Bulk enroll issues up to 500 broker round-trips inside the request | MEDIUM | traced |
| DATA-07 | Appointment-trigger task loads all active workflows and Python-filters | LOW | traced |
| DATA-08 | Recall scanner has no per-institution pacing/jitter | LOW | traced |
| DATA-09 | Recall scanner stub runs hourly but performs no enrollment | INFO | traced |
| DATA-10 | `find_active_recall_workflows` defined but unused | LOW | traced |
| DATA-11 | Webhook always returns 200, masking real errors from monitoring | LOW | self |
| UI-01 | RBAC in the frontend is a client-side redirect, not a block | MEDIUM | traced |
| UI-02 | Archive uses native `confirm()` rather than the app dialog | LOW | traced |
| UI-03 | API error handling is generic toast only; no error boundary | LOW | traced |
| UI-04 | No live updates (SSE); campaign detail is manual-refresh only | MEDIUM | traced |
| UI-05 | Flat routes instead of the plan's sub-route structure | LOW | traced |
| UI-06 | Backend enroll/run-cancel routes exist but no UI consumes them | INFO | traced |
| UI-07 | Plan 08 analytics/CSV/ops/usage surfaces not built (partial, as claimed) | INFO | traced |
| UI-08 | Plan 02 visual builder not started (accurate) | INFO | traced |
| XCUT-01 | Thin integration/E2E coverage — the pattern behind ENG-01 & TPL-01 | HIGH | self |
| XCUT-02 | "Complete" in records means "code written," not "verified working" | HIGH | self |
| XCUT-03 | Compliance gate is NoOp everywhere; nothing enforces consent/caps | HIGH | self |
| XCUT-04 | Built system is a foundation; cannot send a real message end-to-end | INFO | self |
| SCOPE-01 | Channels 03 (voice) / 04 (SMS) / 05 (email) not started | INFO | self |
| SCOPE-02 | Plan 07 AI callback not started | INFO | self |
| SCOPE-03 | Plan 10 per-tenant provisioning not started | INFO | self |
| SCOPE-04 | Plan 11 usage/cost metering not started | INFO | self |
| SCOPE-05 | Plan 12 compliance/consent not started (a Phase-1 peer per the sequence) | HIGH | self |

**Tally:** 4 CRITICAL · 9 HIGH · 12 MEDIUM · 11 LOW · 2 DECISION · 14 INFO = **52 findings**.

---

## A. Plan 01 — Workflow Engine

> Confirmed present and faithful (not defects, recorded for sign-off): immutable versioning
> (`definition_service.py:116`), durable `SKIP LOCKED` timer claiming (`scheduler_service.py:83`),
> guarded run state machine (`runtime_service.py`), dispatcher primitives (`step_dispatcher.py`),
> compliance-gate Protocol seam (`compliance_gate.py:28`), full lifecycle API
> (`automation_workflows.py`), RLS `FORCE`+RBAC on all six tables (`migration 20260702_auto_workflow_core.py`),
> and 13 `test_automation_*` unit files.

### ENG-01 — Durable scheduler never recovers crashed-worker timers
- **Description.** The scheduler exposes `recover_stale_claims()` to reset timers stuck in the
  `CLAIMED` state (the "a worker claimed a due timer, then died before firing it" case). Nothing in
  production ever calls it — there is no Celery task and no beat entry that invokes it.
- **Affected.** `src/app/services/automation/scheduler_service.py:124` (method); `src/app/worker.py`
  beat schedule (only `poll-workflow-timers` and `scan-recall-workflows` are wired);
  `src/app/tasks/automation_workflow.py` (no reference).
- **Why it's a problem.** Durable timer recovery is the core value proposition of the engine. The
  claim/dispatch flow moves a timer to `CLAIMED` before firing; if the worker crashes in that window,
  the timer stays `CLAIMED` forever and the enrollment silently never fires. This is the exact
  failure the mechanism was designed to survive.
- **Expected.** A periodic task (beat) invokes `recover_stale_claims(now=...)` on an interval shorter
  than the claim TTL, returning stuck claims to `PENDING` for re-dispatch.
- **Current.** Method exists and is unit-tested, but is dead code in production; stale claims are never reset.
- **Impact & severity.** CRITICAL. Silent loss of scheduled sends under worker churn; undermines the durability guarantee.
- **Evidence.** `[self-verified]` grep for `recover_stale_claims` → only definition, docstring, and
  `tests/unit/test_automation_scheduler_service.py:100,107,115,122`. No task/beat reference.

### ENG-02 — Emergency-halt capability missing entirely
- **Description.** The plan calls for an emergency-halt that terminates *all in-flight runs* of a
  workflow/version, distinct from `pause` (which only stops new enrollment/advancement). No such
  method, task, or route exists.
- **Affected.** `src/app/services/automation/runtime_service.py`, `src/app/api/routes/automation_workflows.py`
  (only per-run `cancel_run` exists). Plan 08 UI also lists the halt control as absent.
- **Why it's a problem.** Halt is the operator safety valve for a bad campaign (wrong audience, bad
  copy, a compliance incident). Without it, stopping a live blast means cancelling runs one at a time
  or pausing and waiting out in-flight timers.
- **Expected.** `emergency_halt(workflow_version)` that transitions all active/waiting runs to a
  terminal halted state and cancels their pending timers, exposed via an admin route + UI control.
- **Current.** Not implemented; listed under "Remaining" in the session `task_plan.md`.
- **Impact & severity.** HIGH. No fast kill-switch for a misfiring campaign — a real operational/compliance risk once sends are live.
- **Evidence.** `[self-verified]` grep `emergency_halt|halt` in `src/**/*.py` → no matches.

### ENG-03 — Enrollment idempotency is not concurrency-safe
- **Description.** `enroll()` does a SELECT for an existing run by idempotency key, and if none is
  found, INSERTs a new run — with no handling for the unique-constraint violation that occurs when two
  requests race between the SELECT and the INSERT.
- **Affected.** `src/app/services/automation/enrollment_service.py:50-75`; DB unique index on
  `automation_workflow_runs` (migration `20260702_auto_workflow_core.py`).
- **Why it's a problem.** The webhook and bulk-enroll paths rely on idempotency to prevent
  double-enrollment. Under concurrent duplicate events (NexHealth retries, double-clicks, parallel
  workers) both callers pass the SELECT, both attempt INSERT; the second raises `IntegrityError`,
  which is uncaught and surfaces as a 500 instead of gracefully returning the existing run.
- **Expected.** Wrap the INSERT in a try/except on `IntegrityError` (or use an upsert / `ON CONFLICT
  DO NOTHING` + re-SELECT) so a racing duplicate returns `(existing_run, created=False)`.
- **Current.** SELECT-then-INSERT with no `IntegrityError` catch (`enrollment_service.py:57-75`).
- **Impact & severity.** HIGH. Intermittent 500s and failed enqueues under exactly the duplicate-delivery
  conditions idempotency is meant to absorb.
- **Evidence.** `[self-verified]` read `enrollment_service.py:50-91` — SELECT at :51-57, unguarded INSERT at :61-75.

### ENG-04 — Enrollment dedup grain ≠ DB unique-index grain
- **Description.** The in-code dedup SELECT filters on `(institution_id, idempotency_key)`, but the DB
  unique index is `(institution_id, workflow_version_id, idempotency_key)`. The application checks a
  broader key than the constraint enforces.
- **Affected.** `src/app/services/automation/enrollment_service.py:51-57`; unique index in migration `20260702_auto_workflow_core.py`.
- **Why it's a problem.** The two grains disagree about what "duplicate" means. The SELECT treats the
  same key across two versions as a duplicate (returns the first run), while the constraint would allow
  both. This makes the idempotency semantics ambiguous and version-dependent, and it compounds ENG-03
  (the SELECT can't reliably pre-empt the exact collision the constraint raises on).
- **Expected.** The dedup query and the unique constraint use the same column set (whichever grain is
  the intended contract — document it).
- **Current.** Mismatched column sets between query and constraint.
- **Impact & severity.** MEDIUM. Ambiguous idempotency contract; latent correctness risk as versions turn over.
- **Evidence.** `[self-verified]` read of the SELECT columns; `[traced]` unique-index columns from the migration.

### ENG-05 — Inline enroll path hardcodes `location_timezone="UTC"`
- **Description.** The synchronous API enroll path passes `location_timezone="UTC"` rather than the
  clinic's actual timezone, so `CalendarDelay`/quiet-hours math on API-driven enrollments is computed in UTC.
- **Affected.** `src/app/api/routes/automation_workflows.py:309`. (Celery paths resolve tz correctly —
  `src/app/tasks/automation_workflow.py:174-178, 316-320`.)
- **Why it's a problem.** The plan designates location timezone as the authoritative v1 scheduling
  timezone. A UTC assumption shifts "9am local" waits by the clinic's UTC offset, sending at the wrong
  local time for any non-UTC location.
- **Expected.** Resolve the location's timezone (as the Celery paths do) for the inline enroll path too.
- **Current.** Hardcoded `"UTC"` on the inline path only; inconsistent with the async paths.
- **Impact & severity.** HIGH (once sends are live). Wrong-time sends; violates the plan's timezone contract; inconsistent behavior between enrollment routes.
- **Evidence.** `[traced]` `automation_workflows.py:309` vs `tasks:174`.

### ENG-06 — `respect_quiet_hours` flag defined but never enforced
- **Description.** Send/wait node schemas carry a `respect_quiet_hours: bool = True` field, but nothing
  reads it — there is no quiet-hours enforcement anywhere in the dispatcher or scheduler.
- **Affected.** `src/app/services/automation/definition_schema.py:115,125,136,148`. No `QuietHoursService` exists.
- **Why it's a problem.** The field implies a guarantee (no sends during quiet hours) that the engine
  does not deliver. TCPA-style quiet-hours are a legal requirement for outbound messaging; a
  no-op flag is worse than no flag because it reads as "handled."
- **Expected.** A quiet-hours check (per location tz) that defers dispatch when `respect_quiet_hours`
  is true and the current local time is within the quiet window.
- **Current.** Flag is accepted and stored in the definition; never consulted.
- **Impact & severity.** MEDIUM (rises to HIGH when sends go live — legal exposure).
- **Evidence.** `[self-verified]` grep `respect_quiet_hours` across `src/` → only the 4 schema definitions, no readers.

### ENG-07 — `BLOCKED` run/step status is a dead state
- **Description.** The run and step status enums define `BLOCKED = "blocked"`, but no transition ever
  sets a run's/step's `status` to `blocked`. (The related `blocked_reason` *text* field is written, but
  the status itself is set to other values — compliance hold uses `complete_run(outcome=...)`, hard
  block uses `fail_run`.)
- **Affected.** `src/app/models/automation_workflow.py:47,57` (enum values); `runtime_service.py:160`
  and `enrollment_service.py:109` write `blocked_reason` but not `status = BLOCKED`.
- **Why it's a problem.** A status value that is defined and in the CHECK constraint but never used is
  dead state — misleading to future developers and to any UI/analytics that switch on status, and it
  hints at an intended "blocked" lifecycle that was never wired.
- **Expected.** Either the compliance-block path sets `status = BLOCKED`, or the value is removed until it's needed.
- **Current.** `BLOCKED` is defined and constraint-allowed but never assigned; only `blocked_reason` text is set.
- **Impact & severity.** LOW. Maintainability/clarity; latent confusion when compliance blocking is wired.
- **Evidence.** `[self-verified]` grep `BLOCKED|"blocked"|.blocked` → enum defs at models :47/:57; only `blocked_reason` assignments at `runtime_service.py:160`, `enrollment_service.py:109`.

### ENG-08 — DST spring-forward (nonexistent local time) unhandled
- **Description.** `_compute_due_at` builds `datetime.combine(date, time(h,m), tzinfo=tz)` directly. On
  a spring-forward DST transition day, a local wall-clock time in the skipped hour does not exist;
  behavior then relies on zoneinfo defaults rather than an explicit policy.
- **Affected.** `src/app/services/automation/step_dispatcher.py:271` (`_compute_due_at`).
- **Why it's a problem.** Calendar-delay scheduling ("send at 2:30am local") can land on a nonexistent
  or ambiguous local time twice a year, producing an off-by-an-hour or surprising due time.
- **Expected.** Explicit fold/gap handling (e.g., normalize nonexistent times forward, document the fall-back policy).
- **Current.** Implicit; no explicit handling of the DST gap/fold.
- **Impact & severity.** MEDIUM (rare but real; wrong send time on DST days).
- **Evidence.** `[traced]` `step_dispatcher.py:271`.

### ENG-09 — Named engine components from the plan don't exist as modules
- **Description.** The plan names `WorkflowValidationService`, `WorkflowActionRegistry`,
  `WorkflowTriggerRegistry`, and `QuietHoursService` as components. None exist as such: validation is
  folded into the Pydantic `WorkflowDefinition.validate_graph_structure`; there are no action/trigger registries or quiet-hours service.
- **Affected.** `src/app/services/automation/definition_schema.py:191` (validation); absence of the four named modules.
- **Why it's a problem.** For sign-off against the plan, the architecture differs from the documented
  component decomposition. Folding validation into the schema is defensible; the missing registries
  matter when new node/trigger types are added (extensibility seams the plan intended).
- **Expected.** Either the named components, or an updated plan documenting the consolidation.
- **Current.** Consolidated into the Pydantic schema; no registries or quiet-hours service.
- **Impact & severity.** LOW (architecture/maintainability; extensibility seam).
- **Evidence.** `[traced]` per Plan 01 verification pass.

### ENG-10 — No frequency-cap / blast-radius / spend-cap enforcement
- **Description.** There is no enforcement of per-contact frequency caps, campaign blast-radius limits,
  or spend caps. These live behind the compliance gate, which is a NoOp.
- **Affected.** `src/app/services/automation/compliance_gate.py` (NoOp); dispatcher send path.
- **Why it's a problem.** Without caps, a misconfigured or looping workflow can over-message contacts
  or overspend once real channels are wired. This is Plan 12 territory but is a live risk the moment sends turn on.
- **Expected.** Cap checks enforced at enrollment/dispatch via the compliance gate (Plan 12).
- **Current.** No caps; `NoOpComplianceGate` always allows.
- **Impact & severity.** MEDIUM now (by-design deferral) → HIGH at send-enable if still absent.
- **Evidence.** `[traced]` NoOp gate + no cap logic in dispatcher.

### ENG-11 — Create-and-publish instead of draft-first lifecycle *(deviation)*
- **Description.** `create_workflow` calls `create_draft` then immediately `publish_version`, so a new
  workflow is active on creation rather than starting as an editable draft.
- **Affected.** `src/app/api/routes/automation_workflows.py:144-145`.
- **Why it's a problem.** The plan implies a draft-first lifecycle (drafts editable, published versions
  immutable). Create-and-publish means every create goes live immediately — a UX/safety difference the builder (Plan 02) will depend on.
- **Expected.** Product decision: draft-first vs. create-and-publish (the session `task_plan.md` lists this as pending).
- **Current.** Create-and-publish.
- **Impact & severity.** DECISION. Documented open decision, not a silent divergence.
- **Evidence.** `[traced]` route :144-145; `task_plan.md` "Draft-first vs create-and-publish".

### ENG-12 — PATCH re-publishes a new active version inline *(deviation)*
- **Description.** A PATCH that includes a definition immediately publishes a new active version rather than staging a draft.
- **Affected.** `src/app/api/routes/automation_workflows.py:184-185`.
- **Why it's a problem.** Editing a live workflow instantly swaps the active version with no draft/review step; couples to the same open decision as ENG-11.
- **Expected.** Product decision on whether update stages a draft or republishes.
- **Current.** Inline republish on PATCH-with-definition.
- **Impact & severity.** DECISION.
- **Evidence.** `[traced]` route :184-185; `task_plan.md` "PATCH creates new active version immediately".

### ENG-13 — `workflow_enrollment_locks` table not created
- **Description.** The plan mentions an optional `workflow_enrollment_locks` helper table; it was not created.
- **Affected.** migration `20260702_auto_workflow_core.py` (six tables, not seven).
- **Why it's a problem.** Only relevant if the intended enrollment-locking strategy depended on it;
  currently idempotency is via the unique key (see ENG-03/04).
- **Expected.** Table present only if the locking design requires it; plan marked it optional.
- **Current.** Absent.
- **Impact & severity.** INFO (plan-optional).
- **Evidence.** `[traced]` migration table list.

---

## B. Plan 06 — Campaign Templates

> Confirmed present (not defects): four templates in `src/app/services/automation/campaign_templates.py`
> that all validate against the Plan-01 `WorkflowDefinition` schema; list/get/instantiate routes in
> `src/app/api/routes/automation_templates.py` with RBAC; `tests/unit/test_automation_campaign_templates.py`.

### TPL-01 — Template instantiate endpoint raises `TypeError` (broken)
- **Description.** `instantiate_template` calls `create_draft(..., trigger_type=..., definition=...)`,
  but `create_draft` accepts neither parameter and has no `**kwargs`, so every call raises `TypeError`.
- **Affected.** `src/app/api/routes/automation_templates.py:98-103`; `src/app/services/automation/definition_service.py:38-48`.
- **Why it's a problem.** Instantiating a workflow from a campaign template is the entire point of Plan
  06. The endpoint is dead on every invocation.
- **Expected.** `POST /automation/templates/{id}/instantiate` creates a workflow whose active version
  carries the template's trigger + definition, returning the new workflow.
- **Current.** Unconditional `TypeError`.
- **Impact & severity.** CRITICAL. Core Plan-06 feature is non-functional; blocks sign-off of the instantiate slice.
- **Evidence.** `[self-verified]` read `automation_templates.py:98-103` (passes `trigger_type=`,
  `definition=`) and `definition_service.py:38-48` (kwonly params: `name, location_id, description,
  category, is_template, created_by_user_id`; no `**kwargs`).

### TPL-02 — Even without the crash, the template definition would never persist
- **Description.** A second, independent defect behind TPL-01: `create_draft` never creates a version,
  and `AutomationWorkflow.trigger_type`/`.definition` are **read-only properties derived from
  `current_version`**. So even if the kwargs were accepted, the template's definition would not be
  stored and the response `trigger_type` would be `None`.
- **Affected.** `src/app/services/automation/definition_service.py:38-66` (no version created);
  `src/app/models/automation_workflow.py:145,151` (derived read-only properties);
  `automation_templates.py:105-111` (response reads `wf.trigger_type`).
- **Why it's a problem.** Fixing only the signature (TPL-01) would produce a workflow with no version
  and a `None` trigger — a silent, subtler failure. The instantiate flow needs a create-with-version path.
- **Expected.** Instantiate performs `create_draft` **and** `publish_version(definition, trigger_type)`
  (or an equivalent atomic create-with-version), then returns the populated workflow.
- **Current.** No version is created; trigger/definition are unsettable via `create_draft`.
- **Impact & severity.** CRITICAL (root-cause layer of TPL-01).
- **Evidence.** `[self-verified]` `definition_service.py:54-66`; `models/automation_workflow.py:145,151`.

### TPL-03 — Instantiate unit test masks the bug via a mocked service
- **Description.** The route test replaces `AutomationWorkflowDefinitionService` with an `AsyncMock`,
  which accepts any kwargs and returns a fake workflow — so the test passes while the real path is broken.
- **Affected.** `tests/unit/test_automation_campaign_templates.py:153` (and surrounding route tests).
- **Why it's a problem.** The test provides false assurance: "instantiate complete" rests on a test
  that cannot detect the `TypeError` (TPL-01) or the missing-version defect (TPL-02).
- **Expected.** At least one integration test that exercises the **real** service against a test DB and
  asserts a persisted version + non-null trigger.
- **Current.** Service fully mocked; real wiring never exercised.
- **Impact & severity.** HIGH (test-quality; directly caused the undetected CRITICAL).
- **Evidence.** `[traced]` `test_automation_campaign_templates.py:153`.

### TPL-04 — Shipped Reactivation instead of the plan's Sales-Qualification launch campaign
- **Description.** Plan 06's four launch campaigns are Confirmation, Reminder, Recall, **Sales
  Qualification**. The code ships Confirmation, Reminder, Recall + **Reactivation** — a campaign the
  plan lists only under "Future Extensibility." Sales Qualification is (correctly) deferred.
- **Affected.** `src/app/services/automation/campaign_templates.py:146-181`; Plan `06-four-live-campaigns.md:6-11,248`.
- **Why it's a problem.** "Four templates delivered" ≠ the plan's named four. For sign-off, the
  delivered set diverges from the documented launch scope.
- **Expected.** Either deliver the plan's four (with Sales Qualification deferred and noted), or ratify the swap in the plan.
- **Current.** Reactivation substituted for Sales Qualification.
- **Impact & severity.** MEDIUM (scope/plan-alignment, product decision).
- **Evidence.** `[traced]` template registry vs plan campaign list.

### TPL-05 — Template outcome vocabulary diverges from the plan's normalized mapping
- **Description.** Templates use ad-hoc outcomes (`reminder_sent`, `confirmed`, `no_response`,
  `recall_sent`, `booked`, `email_sent`) rather than the plan's normalized outcome taxonomy
  (`confirmed`, `reschedule_requested`, `declined`, `skipped_cancelled`, `failed`, …).
- **Affected.** `src/app/services/automation/campaign_templates.py`; Plan `06-...md:82-85`.
- **Why it's a problem.** Analytics/rollups (Plan 08/11) depend on a consistent outcome vocabulary
  across campaigns; per-template ad-hoc strings will fragment reporting and require remapping later.
- **Expected.** A centralized, normalized outcome mapping shared by all templates.
- **Current.** Per-template ad-hoc outcome strings; no central mapping.
- **Impact & severity.** MEDIUM (reporting correctness/maintainability).
- **Evidence.** `[traced]` template exit-node outcomes vs plan mapping.

### TPL-06 — Hardcoded English copy; no per-tenant / merge-field / consent config
- **Description.** Message bodies (e.g., "Reply STOP to opt out") are hardcoded English literals with
  no per-tenant customization, merge fields (patient/clinic name), or consent-language configuration.
- **Affected.** `src/app/services/automation/campaign_templates.py` (send-node message bodies).
- **Why it's a problem.** Multi-tenant clinics need their own copy/branding and often localization;
  hardcoded strings block that and bake in opt-out language that should be policy-driven.
- **Expected.** Templated copy with merge fields and per-tenant overrides; consent/opt-out text sourced from policy/config.
- **Current.** Static English literals.
- **Impact & severity.** MEDIUM (productization/maintainability; will require template rework).
- **Evidence.** `[traced]` template send-node bodies.

### TPL-07 — Reactivation email template has no unsubscribe / CAN-SPAM footer
- **Description.** The reactivation template's email node carries no unsubscribe link or physical-address/CAN-SPAM footer.
- **Affected.** `src/app/services/automation/campaign_templates.py:173` (reactivation-sms-email-18month email node).
- **Why it's a problem.** Email unsubscribe + sender identification are non-deferrable legal minimums
  (CAN-SPAM; the sequence doc calls unsubscribe the email consent floor). An email campaign template
  without them cannot legally send as-is.
- **Expected.** Every email node includes an unsubscribe mechanism and compliant footer before it can send.
- **Current.** Absent.
- **Impact & severity.** HIGH (legal) — blocks email go-live, though email channel itself is not yet built.
- **Evidence.** `[traced]` reactivation template email node.

### TPL-08 — Templates encode no frequency ceilings; gate is NoOp
- **Description.** Templates set no per-contact attempt/frequency ceilings beyond the schema default
  `max_attempts=1`, and the compliance gate that would enforce caps is a NoOp.
- **Affected.** `campaign_templates.py`; `compliance_gate.py` (NoOp). Plan `06-...md:180-182` requires ≤1/day, ≤3/week.
- **Why it's a problem.** The plan's frequency limits (Plan 12/Finding 3) are neither encoded in the
  templates nor enforced by the gate — nothing prevents over-messaging once channels are live.
- **Expected.** Frequency caps enforced centrally (gate) and/or expressed in templates.
- **Current.** No enforcement.
- **Impact & severity.** MEDIUM (→ HIGH at send-enable).
- **Evidence.** `[traced]` template configs + NoOp gate.

### TPL-09 — Recall/reactivation legal classification unresolved
- **Description.** Whether recall/reactivation outreach is exempt care vs. marketing (consent basis) is
  an open question carried in the session findings and the sequence doc's decision gates.
- **Affected.** Recall/reactivation templates; `implementation_sequence.md` decision gates; session `findings.md`.
- **Why it's a problem.** Consent basis dictates whether these campaigns can send at all and under what
  opt-in; building them "live" without classification is a compliance risk.
- **Expected.** Product/legal classification recorded before recall/reactivation go live.
- **Current.** Unresolved (correctly flagged as a decision gate).
- **Impact & severity.** MEDIUM (product/legal blocker for those campaigns).
- **Evidence.** `[traced]` sequence doc gates + session findings.

### TPL-10 — Template key naming diverges from plan
- **Description.** Template identifiers are hyphenated with time suffixes (`appointment-reminder-24h`)
  vs. the plan's `appointment_reminder` style.
- **Affected.** `campaign_templates.py:146-181`; Plan `06-...md:53`.
- **Why it's a problem.** Minor, but stable identifiers are referenced by API/UI/tests; divergence from
  the plan's naming can cause confusion and mismatched references.
- **Expected.** Consistent, documented key convention.
- **Current.** Hyphenated + time-suffixed keys.
- **Impact & severity.** LOW (naming/consistency).
- **Evidence.** `[traced]` template keys vs plan.

### TPL-11 — Template test suite cannot run locally (missing `structlog`)
- **Description.** During verification the test interpreter lacked `structlog`, so the template test
  suite could not execute in that environment; validity was confirmed by reproducing the schema checks manually.
- **Affected.** test environment/dependencies for `tests/unit/test_automation_campaign_templates.py`.
- **Why it's a problem.** If CI has the same gap, the suite may be effectively unverified; "tests pass"
  needs a runnable environment to mean anything.
- **Expected.** Test dependencies (incl. `structlog`) resolvable in CI/local so the suite actually runs.
- **Current.** Suite not runnable in the verification environment.
- **Impact & severity.** LOW (test-infra; confirm CI actually runs it).
- **Evidence.** `[traced]` verification-pass note.

### TPL-12 — `send_voice` channel intentionally omitted from templates
- **Description.** Templates cover SMS/email nodes; `send_voice` is intentionally omitted because it
  needs a clinic-specific Retell agent id.
- **Affected.** `campaign_templates.py:4` (documented rationale).
- **Why it's a problem.** Not a defect — recorded so sign-off notes that voice campaigns are not represented in templates yet.
- **Expected.** Voice templates added when provisioning (Plan 10) supplies per-clinic agent ids.
- **Current.** Omitted by design.
- **Impact & severity.** INFO.
- **Evidence.** `[traced]` `campaign_templates.py:4`.

---

## C. Plan 09 — Integration & Data Layer

> Confirmed present (not defects): webhook receiver (`nexhealth_webhooks.py:48`), constant-time
> HMAC-SHA256 verify (`:40-41`), tenant-scoped location+contact resolution (`:90-121`),
> `AppointmentTriggerService` with correct ETA math (`appointment_trigger_service.py:59`),
> appointment-trigger Celery task (`automation_workflow.py:357`), bulk enroll with ≤500 cap
> (`automation_workflows.py:403`, `BulkEnrollRequest.items max_length=500`).

### DATA-01 — Webhook signature verification silently disabled when secret unset (default)
- **Description.** `_verify_signature` returns immediately (skips all verification) when
  `settings.nexhealth_webhook_secret` is empty — and the default value is empty.
- **Affected.** `src/app/api/routes/nexhealth_webhooks.py:32-34`; `src/app/config.py:76`
  (`nexhealth_webhook_secret: str = ""`).
- **Why it's a problem.** If the secret env var is unset in production, the appointment webhook accepts
  **unauthenticated** POSTs and enqueues enrollment tasks for any tenant (by supplying a known
  `nexhealth_location_id`). There is no startup guard forcing the secret in production, so a
  misconfiguration silently degrades to an open, unauthenticated trigger endpoint.
- **Expected.** In production, a missing secret is a hard startup failure (or the endpoint rejects all
  requests). Verification is never silently skipped outside explicit local/test.
- **Current.** Empty secret ⇒ verification skipped; empty is the default.
- **Impact & severity.** CRITICAL (security). Unauthenticated cross-tenant enrollment trigger on misconfiguration.
- **Evidence.** `[self-verified]` `nexhealth_webhooks.py:32-34` (`if not secret: return`); `config.py:76` default `""`.

### DATA-02 — No webhook-edge idempotency / replay-protection / payload audit
- **Description.** There is no `nexhealth_webhook_events` (or equivalent) table to claim/dedupe/audit
  inbound events. Every duplicate or replayed `appointment.updated` re-enqueues the trigger task;
  correctness relies solely on downstream enrollment dedup. (Contrast: Retell has `RetellWebhookEvent`
  + a dedicated idempotency module.)
- **Affected.** `src/app/api/routes/nexhealth_webhooks.py` (no event persistence); absence of a NexHealth webhook-event model.
- **Why it's a problem.** No replay protection, no raw/redacted payload audit trail, no per-event
  attempt tracking. Duplicate deliveries do redundant work; a replay attack (given DATA-01) or a
  NexHealth retry storm has no edge-level guard; and there is no forensic record of what was received.
- **Expected.** Persist each event with a claim/idempotency key + status + (redacted) payload, as the
  plan's `NexHealthWebhookService` specifies and as Retell already does.
- **Current.** No edge idempotency/audit; downstream dedup only.
- **Impact & severity.** HIGH (robustness, security, auditability).
- **Evidence.** `[traced]` webhook route; Retell comparison (`RetellWebhookEvent`, `retell/idempotency.py`).

### DATA-03 — `MultipleResultsFound` on location lookup → uncaught 500 → retry storm
- **Description.** Location resolution keys only on `nexhealth_location_id` via `scalar_one_or_none()`,
  ignoring `subdomain`. If two locations share a `nexhealth_location_id`, `scalar_one_or_none()` raises
  `MultipleResultsFound`, which is uncaught → 500. Because NexHealth retries non-2xx, this becomes a retry storm.
- **Affected.** `src/app/api/routes/nexhealth_webhooks.py:93-98`.
- **Why it's a problem.** The plan keys subscriptions on `subdomain + nexhealth_location_id`; dropping
  `subdomain` makes location ids non-unique across tenants and turns a data condition into a 500 loop.
- **Expected.** Scope location lookup by `(subdomain, nexhealth_location_id)`; handle multiple/no matches gracefully (200 ignored).
- **Current.** Single-column lookup; `MultipleResultsFound` unhandled.
- **Impact & severity.** MEDIUM (correctness + availability under a realistic multi-tenant data condition).
- **Evidence.** `[traced]` `nexhealth_webhooks.py:93-98`.

### DATA-04 — Appointment cancellation / reschedule not handled
- **Description.** Cancellations and reschedules arrive as `appointment.updated`, but the webhook
  ignores appointment status and simply schedules enrollment. Already-scheduled enrollments for a
  cancelled/rescheduled appointment are neither cancelled nor recomputed.
- **Affected.** `src/app/api/routes/nexhealth_webhooks.py:71-84,125`; the planned `PmsLiveRevalidationService` does not exist.
- **Why it's a problem.** A patient who cancels can still receive a "your appointment is tomorrow"
  reminder; a reschedule sends at the old time. This is both a correctness and a patient-trust/compliance issue.
- **Expected.** Evaluate appointment status on each update; cancel/reschedule pending enrollments accordingly (or revalidate at send time).
- **Current.** Status ignored; enrollment always scheduled on create/update.
- **Impact & severity.** HIGH (once reminders send — wrong/embarrassing sends).
- **Evidence.** `[traced]` webhook body ignores `status`; no revalidation service.

### DATA-05 — Most of Plan 09's data-layer scope is unbuilt
- **Description.** The plan's core "disposable read model / projection" design and its services are
  absent: no `appointment_working_set` / `recall_eligibility_working_set` projection tables, no
  `nexhealth_webhook_subscriptions`, no `AppointmentProjectionService`, `NexHealthSubscriptionService`,
  `NexHealthReconciliationService`, `PmsLiveRevalidationService`, or backfill jobs.
- **Affected.** `src/app/models/` (no `*nexhealth*` projection models); `src/app/services/` (named services absent).
- **Why it's a problem.** What exists is a thin webhook→enqueue path, not the resilient projection/
  reconciliation/revalidation data layer the plan specifies. The session records are honest about their
  smaller scope, but the *plan's* Plan-09 scope is only partially met — relevant for sign-off and for
  the reconciliation/backfill/live-revalidation the later campaigns depend on.
- **Expected.** The projection read model + subscription lifecycle + reconciliation + revalidation per the plan.
- **Current.** Webhook receiver + trigger service + bulk enroll only.
- **Impact & severity.** HIGH (scope; downstream campaigns depend on revalidation/reconciliation).
- **Evidence.** `[traced]` Glob `src/app/models/*nexhealth*` → none; named services absent.

### DATA-06 — Bulk enroll issues up to 500 broker round-trips inside the request
- **Description.** `bulk_enroll` loops over up to 500 items and calls `apply_async` per item
  synchronously before returning 202.
- **Affected.** `src/app/api/routes/automation_workflows.py:435-450`.
- **Why it's a problem.** Up to 500 synchronous broker round-trips inside a request hold the connection
  and add latency; the ≤500 cap is the only backpressure. A group/chord or a single fan-out task would be cleaner and faster.
- **Expected.** Batch enqueue (Celery `group`/`chord`) or a single fan-out task that enqueues internally.
- **Current.** Per-item `apply_async` in a request-time loop.
- **Impact & severity.** MEDIUM (scalability/latency; acceptable at 500, not beyond).
- **Evidence.** `[traced]` `automation_workflows.py:435-450`.

### DATA-07 — Appointment-trigger task loads all active workflows and Python-filters
- **Description.** `trigger_appointment_workflows` loads all active appointment workflows and filters
  by `trigger_type` in Python rather than in SQL, per webhook event.
- **Affected.** `src/app/tasks/automation_workflow.py:414`; `appointment_trigger_service.py:37-40`.
- **Why it's a problem.** Linear per-event scan; fine at low workflow counts, but grows with the number
  of active workflows across all institutions and runs on every appointment webhook.
- **Expected.** Filter by `trigger_type` (and institution) in the SQL query.
- **Current.** Load-all + Python filter.
- **Impact & severity.** LOW→MEDIUM (scalability).
- **Evidence.** `[traced]` task + service.

### DATA-08 — Recall scanner has no per-institution pacing/jitter
- **Description.** The recall scanner scans active workflows table-wide with no per-institution pacing
  or jitter (the plan requires paced/jittered scanning). Currently harmless because it is a no-op stub.
- **Affected.** `src/app/tasks/automation_workflow.py:498-508`.
- **Why it's a problem.** When the real recall query lands, an unpaced table-wide scan can spike load
  and hit external rate limits; the pacing requirement must be added with the real logic.
- **Expected.** Per-institution paced/jittered scanning per the plan.
- **Current.** Table-wide, unpaced (stub).
- **Impact & severity.** LOW (latent; only matters when the query is implemented).
- **Evidence.** `[traced]` scanner stub.

### DATA-09 — Recall scanner stub runs hourly but performs no enrollment
- **Description.** `scan_recall_workflows` is wired into the beat schedule (hourly) but only counts/logs
  active `recall_scan` workflows; the real patient-history query and enrollment are not implemented.
- **Affected.** `src/app/tasks/automation_workflow.py:473-518`; `src/app/worker.py:61-64` (beat entry).
- **Why it's a problem.** Not a defect — but sign-off should note recall "runs" without doing anything,
  so it must not be mistaken for a working recall campaign.
- **Expected.** Real per-institution recall eligibility query + enrollment (Plan 09 remaining).
- **Current.** No-op stub on an hourly beat.
- **Impact & severity.** INFO (by-design stub, honestly documented).
- **Evidence.** `[traced]` task :473-518 + beat :61-64.

### DATA-10 — `find_active_recall_workflows` defined but unused
- **Description.** `find_active_recall_workflows` exists but is not called by the recall stub.
- **Affected.** `src/app/services/automation/appointment_trigger_service.py:42`.
- **Why it's a problem.** Dead-until-wired helper; minor, but flags the incomplete recall path.
- **Expected.** Used by the real recall scanner.
- **Current.** Unused.
- **Impact & severity.** LOW.
- **Evidence.** `[traced]` service :42 (no callers in the stub).

### DATA-11 — Webhook always returns 200, masking real errors from monitoring
- **Description.** The endpoint returns 200 for unknown location, unknown contact, and ignored events
  (deliberately, to avoid NexHealth deactivating the endpoint on non-2xx).
- **Affected.** `src/app/api/routes/nexhealth_webhooks.py:57-59,74,100-106`.
- **Why it's a problem.** The design choice is reasonable, but "always 200" also hides genuine
  misconfigurations (e.g., a location that *should* resolve but doesn't) from HTTP-level monitoring.
  Without the audit table (DATA-02), these silent "ignored" outcomes are unobservable.
- **Expected.** Keep 200, but record ignored/failed outcomes (metrics + the DATA-02 audit table) so silent drops are visible.
- **Current.** 200 with `{"status":"ignored"}` bodies; no metric/audit persistence.
- **Impact & severity.** LOW (observability; compounds DATA-02).
- **Evidence.** `[self-verified]` `nexhealth_webhooks.py:57-59,72-74` (always-200 pattern).

---

## D. Plan 08 (Campaign UI) & Plan 02 (Builder)

> Confirmed present (not defects): `pages/Campaigns.tsx`, `pages/CampaignDetail.tsx`,
> `lib/automation-api.ts` (6 endpoints, all mapped to real backend routes, no dead calls),
> `components/app-sidebar.tsx:107` nav (INSTITUTION_ADMIN only), `router.tsx:269` RoleGuard.

### UI-01 — RBAC in the frontend is a client-side redirect, not a block
- **Description.** `RoleGuard` redirects non-`INSTITUTION_ADMIN` users to their home page rather than
  hard-blocking; it is a UX guard, not a security boundary.
- **Affected.** `nexus-dashboard-web/src/components/RoleGuard.tsx:16`; `router.tsx:269-281`.
- **Why it's a problem.** Client-side guards can be bypassed; security must be enforced server-side.
  (Mitigated: the backend automation routes independently enforce role via `get_current_institution_user`,
  verified in Plan 01 — so this is defense-in-depth, not an open hole. Recorded so sign-off notes the reliance on backend enforcement.)
- **Expected.** Client guard for UX + authoritative backend enforcement (present).
- **Current.** Client redirect + backend enforcement.
- **Impact & severity.** MEDIUM (documented reliance; verify backend coverage for every campaign route).
- **Evidence.** `[traced]` `RoleGuard.tsx:16`; backend deps from Plan 01 pass.

### UI-02 — Archive uses native `confirm()` rather than the app dialog
- **Description.** The archive confirmation is a browser `confirm()` prompt, not the app's styled Dialog component.
- **Affected.** `nexus-dashboard-web/src/pages/Campaigns.tsx:104`; `pages/CampaignDetail.tsx:122`.
- **Why it's a problem.** Inconsistent UX vs. the rest of the app; the session wording "archive confirm dialog" implies a styled modal.
- **Expected.** App Dialog component for the confirmation.
- **Current.** Native `confirm()`.
- **Impact & severity.** LOW (UX/consistency).
- **Evidence.** `[traced]` `Campaigns.tsx:104`, `CampaignDetail.tsx:122`.

### UI-03 — API error handling is generic toast only; no error boundary
- **Description.** API failures surface as generic `toast.error` messages; a failed detail load renders "Campaign not found."
- **Affected.** `pages/Campaigns.tsx`, `pages/CampaignDetail.tsx`.
- **Why it's a problem.** Generic errors hide root causes (403 vs 500 vs network) from operators; no error boundary for render failures.
- **Expected.** Differentiated error states + an error boundary.
- **Current.** Generic toast; "not found" catch-all.
- **Impact & severity.** LOW (UX/observability).
- **Evidence.** `[traced]` page error handling.

### UI-04 — No live updates (SSE); campaign detail is manual-refresh only
- **Description.** The detail page refreshes via `useEffect`/a Refresh button; it does not subscribe to
  the app's SSE stream (`useSSE`) for `workflow_runs_updated`.
- **Affected.** `pages/CampaignDetail.tsx` (no `useSSE` wiring); `hooks/useSSE.ts` exists and is used elsewhere.
- **Why it's a problem.** Run/enrollment progress is stale until manual refresh — weak for a "progress" UI whose value is watching sequences advance.
- **Expected.** Live updates via SSE for run/enrollment changes.
- **Current.** Manual refresh only.
- **Impact & severity.** MEDIUM (UX; core to the "progress" purpose).
- **Evidence.** `[traced]` detail page lacks `useSSE`.

### UI-05 — Flat routes instead of the plan's sub-route structure
- **Description.** Routes are flat (`/institution-admin/campaigns[/:id]`) rather than the plan's
  `/overview /enroll /runs /analytics` sub-routes.
- **Affected.** `nexus-dashboard-web/src/router.tsx:269-281`.
- **Why it's a problem.** Diverges from the planned IA; will need restructuring when the fuller Plan-08 surfaces land.
- **Expected.** Sub-route structure per the plan (as those surfaces are built).
- **Current.** Two flat routes.
- **Impact & severity.** LOW (IA/forward-compat).
- **Evidence.** `[traced]` `router.tsx:269-281`.

### UI-06 — Backend enroll/run-cancel routes exist but no UI consumes them
- **Description.** Backend `POST .../{id}/enroll` (`automation_workflows.py:251`), batch enroll (`:398`),
  `GET .../runs/{run_id}` (`:336`), and cancel-run (`:354`) are never called from the frontend.
- **Affected.** frontend `lib/automation-api.ts` (6 calls only) vs. backend routes above.
- **Why it's a problem.** Not a defect — but flags a UI/back-end capability gap (no manual enroll, no
  run-detail, no per-run cancel in the UI), consistent with the "partial" claim.
- **Expected.** UI for enroll/run-detail/cancel in later Plan-08 work.
- **Current.** Backend-only; unused by UI.
- **Impact & severity.** INFO (capability gap, matches partial claim).
- **Evidence.** `[traced]` API wrapper vs backend routes.

### UI-07 — Plan 08 analytics/CSV/ops/usage surfaces not built (partial, as claimed)
- **Description.** No analytics/progress charts, no CSV/manual enrollment UI, no run-detail timeline, no
  operations/emergency-halt page, no usage/cost views.
- **Affected.** `nexus-dashboard-web/src/pages/` (absent pages).
- **Why it's a problem.** Not a defect — these are the deferred Plan-08 surfaces; recorded so sign-off reflects true coverage.
- **Expected.** Built in the full Plan-08 phase (depends on Plans 06/11/12 outcomes).
- **Current.** Absent (matches "partial").
- **Impact & severity.** INFO.
- **Evidence.** `[traced]` absence confirmed.

### UI-08 — Plan 02 visual builder not started (accurate)
- **Description.** No builder/canvas route, no graph library (`react-flow`/`@xyflow`/`dagre`/`elkjs`) in
  `package.json`, no workflow-authoring API client. Backend `PATCH /{id}` and `POST /{id}/publish` exist but no UI consumes them.
- **Affected.** `nexus-dashboard-web/` (no builder); `package.json` (no graph lib).
- **Why it's a problem.** Not a defect — confirms the "not started" claim; recorded for completeness.
- **Expected.** Built in Phase 6 per the sequence.
- **Current.** Placeholder session folder only.
- **Impact & severity.** INFO.
- **Evidence.** `[traced]` grep for builder/graph-lib → none.

---

## E. Cross-cutting

### XCUT-01 — Thin integration/E2E coverage (the pattern behind ENG-01 & TPL-01)
- **Description.** Two of the most serious findings — a broken endpoint (TPL-01/02) and an unscheduled
  recovery mechanism (ENG-01) — both passed unit tests while being non-functional in reality, because
  tests either mock the collaborator (TPL-03) or test a method in isolation without asserting it is wired to run.
- **Affected.** `tests/unit/test_automation_*` (unit-only; mocked services); no integration tests that drive enroll→timer→dispatch or template→instantiate against a real DB/broker.
- **Why it's a problem.** Unit coverage is good but gives false confidence about *wiring*. The current
  suite cannot detect "method exists but nothing calls it" or "route calls a signature that doesn't exist."
- **Expected.** Integration/E2E tests exercising real services for the critical paths (enroll→timer→dispatch, template→instantiate, webhook→enqueue→enroll, stale-claim recovery).
- **Current.** Unit-only with mocks; no wiring-level coverage.
- **Impact & severity.** HIGH (process/quality; directly enabled the two CRITICALs).
- **Evidence.** `[self-verified]` TPL-03 (`test_...:153` mocks service); ENG-01 (recovery only referenced by tests).

### XCUT-02 — "Complete" in records means "code written," not "verified working"
- **Description.** Several items marked "complete" are complete-as-authored but not exercised end-to-end
  (TPL-01 instantiate; ENG-01 recovery; ENG-05 tz on the inline path).
- **Affected.** session `task_plan.md`/`progress.md` for Plans 01, 06.
- **Why it's a problem.** For sign-off, "complete" must mean "verified working." The current
  definition-of-done allows non-functional code to be marked done.
- **Expected.** Definition-of-done includes an executed end-to-end verification per completed item.
- **Current.** "Complete" = code present + unit tests pass (which can be mocked).
- **Impact & severity.** HIGH (process; affects trust in every "complete" mark).
- **Evidence.** `[self-verified]` cross-reference of records vs TPL-01/ENG-01.

### XCUT-03 — Compliance gate is NoOp everywhere; nothing enforces consent/caps
- **Description.** Every send path checks `NoOpComplianceGate`, which always allows; enrollment-level
  compliance is a documented placeholder. No consent, frequency-cap, quiet-hours (ENG-06), or content validation is enforced.
- **Affected.** `src/app/services/automation/compliance_gate.py:42`; `enrollment_service.py:22-25`; dispatcher send path.
- **Why it's a problem.** The seam is clean (a strength), but the entire compliance surface (Plan 12) is
  absent. Nothing prevents non-consented/over-frequency/quiet-hours-violating sends the moment channels turn on.
- **Expected.** `ComplianceGateService` (Plan 12) implementing consent + caps + quiet-hours + content
  validation, wired into enrollment and dispatch before any channel sends.
- **Current.** NoOp everywhere (by design for this phase), but a hard blocker for send-enable.
- **Impact & severity.** HIGH (by-design deferral, but a go-live gate — see SCOPE-05).
- **Evidence.** `[self-verified]` NoOp gate; quiet-hours unenforced (ENG-06).

### XCUT-04 — Built system is a foundation; cannot send a real message end-to-end
- **Description.** Sends are stubs (`step_dispatcher.py:217 _dispatch_send_stub`, `result_code="stub_dispatched"`);
  no channel (SMS/voice/email), provisioning, or compliance exists. The system schedules and records intent but sends nothing.
- **Affected.** dispatcher send path; absent Plans 03/04/05/10/12.
- **Why it's a problem.** Not a defect — a scope statement for sign-off: this is engine + scaffolding, not a shippable outbound product.
- **Expected.** Real sends after channels + provisioning + compliance land (later phases).
- **Current.** Stubbed sends only.
- **Impact & severity.** INFO (scope reality).
- **Evidence.** `[self-verified]` send stub.

---

## F. Not-started scope (accurate per records — recorded for sign-off)

### SCOPE-01 — Channels 03 (voice) / 04 (SMS) / 05 (email) not started
- No channel send handlers exist; the dispatcher send node is a stub. SMS is the sequence's designated
  first channel and gates the first live campaign. **Impact:** HIGH for the roadmap (nothing sends without these). INFO as a status. `[self-verified]`

### SCOPE-02 — Plan 07 AI callback not started
- No AI-callback workflow template/mode. Depends on 01/03/12. INFO. `[self-verified]`

### SCOPE-03 — Plan 10 per-tenant provisioning not started
- No credential resolver / readiness model for per-tenant Twilio/email/Retell. The sequence flags its
  vendor registrations (A2P/toll-free/domain warm-up) as day-1 long-lead — **the real critical path.**
  **Impact:** HIGH for the roadmap. INFO as a status. `[self-verified]`

### SCOPE-04 — Plan 11 usage/cost metering not started
- No metering ingestion or rollups. The sequence wants metering shipped *with* the first channel to
  accumulate history — starting it late loses data. MEDIUM for the roadmap. INFO as a status. `[self-verified]`

### SCOPE-05 — Plan 12 compliance/consent not started (a Phase-1 peer per the sequence)
- No consent schema, `ComplianceGateService`, frequency caps, quiet-hours enforcement, or content
  validation. The sequence explicitly calls Plan 12 a **Phase-1 peer of Plan 01** whose consent-schema
  migration must land **before any channel sends** (see XCUT-03, ENG-06, ENG-10, TPL-07/08).
  **Impact:** HIGH — a hard go-live gate for every channel. `[self-verified]`

---

## Appendix 1 — Verification method
graphify-oriented navigation (fresh code-only graph), then file/line inspection. Four independent
verification passes (Plans 01, 06, 08/02, 09) each read the plan + session record and traced code.
The highest-impact code-level claims were re-read by the author this session and are tagged
`[self-verified]`: ENG-01, ENG-02, ENG-03/04, ENG-06, ENG-07, TPL-01/02, DATA-01, DATA-11, plus the
cross-cutting/scope observations. Remaining findings are `[traced]` with a cited `file:line`.

## Appendix 2 — Confirmed-correct implementation (for positive sign-off)
Recorded so the register also captures what was verified as correctly built:
immutable versioning; durable `SKIP LOCKED` timer claiming; guarded run state machine; dispatcher
wait/condition/exit primitives; compliance-gate Protocol seam; full lifecycle API with per-route RBAC;
RLS `FORCE` + scoped policies + grants + indexes on all six engine tables; four schema-valid campaign
templates; constant-time HMAC webhook verification (when a secret is set); correct appointment-offset
ETA math; ≤500 bulk-enroll cap; a read-only + lifecycle campaign UI whose 6 API calls all map to real
backend routes with no dead/mismatched calls; and honest session records whose stub/partial/not-started
labels matched reality in every case checked.

## Appendix 3 — Suggested resolution order
1. TPL-01 + TPL-02 (fix instantiate; add real-service integration test) — CRITICAL, small.
2. ENG-01 (schedule `recover_stale_claims`) — CRITICAL, small.
3. DATA-01 (production secret guard) — CRITICAL, small.
4. ENG-03/04 (concurrency-safe idempotency) — HIGH, small.
5. ENG-05 (inline-enroll timezone) — HIGH, small.
6. DATA-04 + DATA-03 + DATA-02 (cancellation, multi-match, webhook idempotency/audit) — HIGH/MEDIUM.
7. ENG-02 (emergency halt) + ENG-06 (quiet-hours) — HIGH, coordinate with Plan 12.
8. XCUT-01 (integration/E2E harness) — HIGH, process; do alongside 1–2.
9. Remaining MEDIUM/LOW as the relevant phases resume; ratify DECISION items (ENG-11/12, TPL-04).
