# Plan 06 — Four Live Campaigns — Verification Findings

Audited: 2026-07-03. Evidence-based against actual codebase (branch `ali/phase-2`).

## Plan intent (from `docs/new_work/Implementation Plans/06-four-live-campaigns.md`)
Build FOUR launch campaigns as configurable workflow templates:
1. Appointment Confirmation (time-offset trigger, PMS revalidate, write-back confirmation status)
2. Appointment Reminder (time-offset trigger, PMS revalidate)
3. Overdue Patient Recall (recurring recall eligibility scan + manual/CSV enrollment, multi-touch drip)
4. Sales Qualification (manual/CSV/new-contact enrollment; inbound lead deferred)

Plus: `workflow_templates` + `workflow_template_versions` model (DB rows or seed defs), per-campaign
guided config, campaign-specific validation on top of generic, outcome mapping, PMS revalidation before
appointment sends, PMS write-back for confirmation, recall eligibility, template clone flow, analytics
rollups, staging/test mode, frequency-cap-respecting defaults (Part 12).

## What actually exists

### Template registry — `src/app/services/automation/campaign_templates.py`
- A checked-in **Python dataclass registry** (`CampaignTemplate`, `TEMPLATES` dict), NOT DB tables.
  No `workflow_templates` / `workflow_template_versions` migration exists (grep of `alembic/` → none).
  No versioning, no `content class`, no `supported channels`, no `minimum provisioning`, no active flag.
- Four templates defined — but NOT the four the plan names:
  - `appointment-reminder-24h` (L146) — SMS only, single send → exit. trigger `appointment_offset` offset_hours=-24.
  - `appointment-confirmation-48h` (L154) — SMS + wait 2h + condition on `appointment_status == confirmed` + two exits. trigger `appointment_offset` offset_hours=-48.
  - `recall-sms-6month` (L165) — single SMS → exit. trigger `recall_scan` interval 6mo.
  - `reactivation-sms-email-18month` (L173) — SMS + wait 48h + condition on `appointment_booked` + email fallback. trigger `recall_scan` interval 18mo.
- **Sales Qualification: DOES NOT EXIST.** Replaced by a Reactivation template. No manual/CSV/new-contact
  qualification template, no qualify→book→handoff branch.
- Voice templates intentionally excluded (module docstring L4-7): voice needs clinic-specific Retell agent id.

### API — `src/app/api/routes/automation_templates.py`
- `GET /automation/templates` (list), `GET /automation/templates/{id}`, `POST /{id}/instantiate`.
- `instantiate` creates a draft then `publish_version(template.definition)` → workflow goes **active immediately**
  (L88-118). No draft-from-template lifecycle; docstring notes callers must pause manually. RBAC via
  `get_current_institution_or_location_admin` (read) / `get_current_institution_user` (instantiate).

### Trigger wiring
- **Appointment offset — REAL & wired.** `src/app/api/routes/nexhealth_webhooks.py:123-136` enqueues
  `trigger_appointment_workflows.delay(...)` on NexHealth appointment webhook events. Task at
  `src/app/tasks/automation_workflow.py:353-460` calls `AppointmentTriggerService.find_active_appointment_workflows`
  (`appointment_trigger_service.py:26-40`), computes ETA via `compute_enrollment_eta` (offset_hours), and
  schedules `enroll_and_start_workflow_run` with idempotency key. This path is genuinely functional.
- **Recall scan — STUB.** `scan_recall_workflows` (`automation_workflow.py:468-527`) scheduled hourly in
  celery beat (`worker.py:61-64`). It only *counts* active recall workflows per institution and logs a summary;
  it does NOT query patient visit history, does NOT enroll any patient. Explicit NOTE L517-519: "Real recall
  enrollment requires querying patient visit history from NexHealth ... Wire in ... when NexHealth sync is ready."
  `find_active_recall_workflows` (`appointment_trigger_service.py:42-56`) exists but is not called by the scan.
- **Manual/CSV enrollment for recall/sales-qual — not present** in this plan's scope (no CSV importer here).
- **Inbound lead / new-contact trigger for Sales Qualification — absent** (campaign itself absent).

### PMS revalidation & write-back — MISSING
- `AppointmentTriggerService` docstring (L1-6) explicitly: "Does not make NexHealth API calls." No
  revalidation step runs before sends. grep for `revalidat|write_back|update_appointment_status|set_confirmed`
  across `src/app/services/automation` → nothing.
- The confirmation template's condition `appointment_status == "confirmed"` is evaluated against the run
  `context` dict (`step_dispatcher.py:243-262`, `_evaluate_rule` → `context.get(rule.field)`). Nothing in the
  run populates `appointment_status` from PMS during the run, so the condition effectively always takes the
  false branch → `no_response`. The "if confirmed, write to PMS and exit" behavior is not implemented at all.
  Same for reactivation's `appointment_booked` field.
- No PMS confirmation write-back exists anywhere.

### Compliance / frequency cap — partial, present
- Sends go through `ComplianceGateService.check` before dispatch (`step_dispatcher.py:117-130`): block / hold /
  allow. This is Plan 12 wiring, present. Templates include `Reply STOP to opt out`. Frequency-cap-specific
  defaults in templates are not explicitly encoded (attempt counts are low by construction).

### Outcome mapping — minimal
- Exit nodes carry ad-hoc outcome strings (`reminder_sent`, `confirmed`, `no_response`, `recall_sent`,
  `booked`, `email_sent`). There is NO centralized normalized outcome map matching the plan's four outcome
  vocabularies (e.g. `reschedule_requested`, `attempts_exhausted`, `qualified_booked`). No branching on
  reschedule/handoff.

### Analytics rollups, staging/test mode, per-campaign guided config — not in this deliverable
- No campaign-specific validation layer on top of generic schema validation. No guided settings model
  (channel order, quiet hours, retry limits, PMS recheck rules, staff handoff) — templates are static dicts.
- Template preview/sample-data and dry-run "test mode" not implemented for templates (test-run dialog exists
  in UI but general workflow, not template staging fixtures).

## Tests — `tests/unit/test_automation_campaign_templates.py`
- 17 tests, ALL PASS (verified: `JWT_SECRET=... ENCRYPTION_KEY=... pytest` → 17 passed).
- Cover: every template validates against `WorkflowDefinition` schema; exactly the 4 keys present
  (`test_all_four_templates_present` hard-codes reactivation, not sales_qualification); trigger types;
  reactivation multi-exit; response model; list/get/instantiate route handlers incl. 404 and the instantiate
  regression (create_draft must not get trigger_type/definition, must publish_version).
- NOT covered: no PMS revalidation test, no recall enrollment test, no confirmation write-back, no
  outcome-mapping test, no clone-into-tenant RLS test, no dry-run of full campaign execution, no
  "skip on cancelled appointment" test. `test_automation_plan09.py` covers appointment trigger service.

## Scope alignment
Session docs (`outbound-06-campaign-templates/`) are honest: they claim reminder + confirmation + recall-6mo +
reactivation-18mo, template list/get/instantiate, schema tests — and list "wire real send handlers", "PMS
recheck rules", legal review as remaining. The scope doc §10 names Sales Qualification as deferred (manual/CSV),
but the plan still asked for a manual/CSV Sales Qualification *template*; the implementation shipped a
Reactivation template instead and dropped Sales Qualification entirely. Reactivation is a reasonable Phase-2
addition but is NOT one of the four scoped launch campaigns.
