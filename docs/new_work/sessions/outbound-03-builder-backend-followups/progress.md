# Progress: Outbound 03 — Builder Backend Follow-ups

## Session start — 2026-07-03
- **Context:** `outbound-02-builder-ui` shipped the Builder frontend in an isolated
  lane (no backend touched) and deferred 7 follow-ups + 2 trade-offs to Plans 01/06/10.
- **This session:** investigate each against the live backend, implement all
  independent/unblocked items, defer the rest with code-grounded reasons.

---

## Slice 1 — Investigation (Phase 0) — complete
Read the automation subsystem end-to-end (routes, definition service, schema,
templates, dispatcher, models, existing tests). Findings recorded in
`findings.md`. Verdicts: **implement #1, #2, #6; defer #3, #4, #5, #7, T1, T2.**

Two discoveries reshaped the plan vs. the outbound-02 §5 framing:
1. **No merge-field renderer exists** — `step_dispatcher._dispatch_send_stub` is a
   stub (Plans 03/04/05). This moved **#3 (merge-field catalog)** from "easy win"
   to **deferred/blocked** — the authoritative field set is undefined.
2. **No draft-with-definition** — a definition only lives in a published version and
   publishing forces ACTIVE. This is the root cause of both **#1** (instantiate) and
   **#7**, and bounds what "fixing instantiate" can mean.

---

## Slice 2 — Implementation (Phases 1–3) — complete

### #1 — `instantiate` fix — `src/app/api/routes/automation_templates.py`
- Root cause: route passed `trigger_type=`/`definition=` kwargs that `create_draft`
  does not accept → `TypeError` on every call; and `create_draft` never persists a
  definition.
- Fix: `create_draft(name, created_by_user_id)` → `publish_version(template.definition,
  published_by_user_id)`, mirroring `create_workflow`. Now returns `WorkflowResponse`
  (was an ad-hoc `dict`); response_model updated. Result is an **active** workflow;
  docstring documents that a true draft awaits #7.
- Imported `WorkflowResponse` from `automation_workflows` (no import cycle —
  workflows route does not import templates).

### #2 — Validate endpoint — `src/app/api/routes/automation_workflows.py`
- New `POST /automation/workflows/validate` (declared before the `/{workflow_id}`
  routes). Body `{definition}`, returns `{valid, issues[]}`.
- New models: `ValidateDefinitionRequest`, `ValidationIssueResponse`
  `{severity, node_id, field_path, message}`, `ValidateDefinitionResponse`.
- Helpers `_node_id_for_loc` (maps pydantic `("nodes", idx, …)` loc → node `id`) and
  `_issue_from_pydantic_error` (strips the "Value error, " prefix). Reuses
  `WorkflowDefinition.model_validate` — backend stays authoritative.

### #6 — Version-list — `src/app/api/routes/automation_workflows.py`
- New `GET /automation/workflows/{id}/versions` → newest-first history.
- New model `WorkflowVersionResponse` (incl. `is_current`). Reuses `_get_workflow_or_404`
  and the eager `wf.versions` relationship; no new DB access.

**Imports touched:** `automation_workflows.py` — added `Literal` (typing) and
`ValidationError` (pydantic).

---

## Slice 3 — Validation & testing (Phase 4) — complete

**Environment:** global Python 3.14; installed project deps via `pip install -e .`
(exit 0). Test env vars set: `JWT_SECRET`, `NEXHEALTH_*`, `RETELL_API_SECRET`, `APP_ENV=test`.

**Tests added (7 new):**
- `test_automation_campaign_templates.py`: rewrote `test_instantiate_creates_draft_workflow`
  → `test_instantiate_creates_and_publishes_workflow` — asserts create_draft is called
  WITHOUT `trigger_type`/`definition` (regression guard for the original bug), that
  `publish_version` receives the template definition, and that the result is active.
- `test_automation_workflow_routes.py`: `test_validate_accepts_valid_definition`,
  `test_validate_reports_missing_exit_node`, `test_validate_links_field_error_to_node_id`,
  `test_list_versions_returns_newest_first_with_current_flag`,
  `test_list_versions_workflow_not_found_raises_404`.

**Results:**
- `pytest test_automation_workflow_routes.py test_automation_campaign_templates.py` →
  **38 passed**.
- `pytest` across all 4 automation suites (routes, templates, models, task) →
  **55 passed**, 0 failures.
- App import smoke check → OK; new routes present:
  `/automation/workflows/validate`, `/automation/workflows/{workflow_id}/versions`.
- `ruff check` on the 4 touched files → **6 pre-existing F401** unused imports in
  `test_automation_workflow_routes.py` (`WorkflowUpdateRequest`, `_get_workflow_or_404`,
  `archive_workflow`, `pause_workflow`, `resume_workflow`, `update_workflow`) — present
  before this session, left untouched for surgical scope. **My additions introduce
  zero new lint errors** (all new imports are used).
- Pre-existing, unrelated: `test_locations_routes.py` / `test_nexhealth_client.py` fail
  to collect due to a missing test-only dep (`respx`) — not caused by this change.

---

## Deferred items (documented, non-blocking) — see findings.md §3
| # | Item | Blocker |
|---|------|---------|
| 3 | Merge-field catalog | No renderer — send handlers stubbed (Plans 03/04/05 own the field set). |
| 4 | Channel readiness | Voice needs Retell agent provisioning; "ready" contract unspecified. |
| 5 | Server test-run | Plan-06 runtime dependency + missing send-handler renderer. |
| 7 | Draft-with-definition | Product decision + schema migration (root cause of #1's semantics). |
| T1 | Optimistic lock | Concurrency-contract decision + frontend token coordination. |
| T2 | `send_voice` seed | Needs a clinic Retell agent id (same as #4). |

## Session outcome
3 dependency-free backend follow-ups implemented and green (#1 bug fix, #2 validate,
#6 version-list); 6 items deferred with concrete, code-grounded blockers. Frontend
untouched. **Status: implementation complete; finalizing graph refresh.**

---

## Slice 4 — Post-merge re-evaluation (Plans 04/05) — 2026-07-03
- Backed up all work to branch `ali/phase-2` (commit `6177641`, pushed).
- Verified + merged the other dev's `feature/outbound-engagement-engine`
  (Plan 04 SMS `73c5a78`, Plan 05 email `802895e`) into `ali/phase-2`. Conflict
  check first (`git merge-tree`, 0 file overlap) → clean merge `4732361`. Merged
  tree green (88 automation tests pass).
- **Impact on deferrals:** Plan 04/05 shipped `template_renderer.py` — the
  send-handler renderer whose absence was the blocker for #3 and #5. Analyzed the
  merged files (renderer + SMS/email executors + dispatcher diff). Full detail and
  the authoritative merge-field set in `findings.md §6`.
- **Reclassified:** #3 (merge-field catalog) → **now actionable**; #5 (server
  test-run) → **preview half unblocked, dry-run half still Plan-06-gated**; #4/T2
  still blocked (voice still stubbed).
- No code implemented in this slice — reclassification + docs only, pending
  go-ahead on #3.

---

## Slice 5 — Implement #3 merge-field catalog (now unblocked) — 2026-07-03

### Analysis of the merged dev files (Plans 04/05)
- `template_renderer.render_sms_body` is the single merge function; the email
  executor uses it for **both** subject and body (`email_node_executor.py:89-90`),
  so SMS + email share one contract.
- Static fields resolved: `patient_first_name`, `patient_last_name`,
  `patient_full_name` (Contact), `clinic_name` (Location). Context/trigger keys
  are also substitutable but dynamic. Voice still stubbed (Plan 03).

### Change — single source of truth + endpoint
- `src/app/services/automation/template_renderer.py`: introduced
  `MergeFieldSpec` + `STATIC_MERGE_FIELDS` (name, label, description, sample,
  group, source, resolver). `render_sms_body` now builds its substitutions by
  iterating this list — so the catalog and the renderer are physically the same
  source and cannot drift. **Behaviour preserved:** a static field is only set
  when its source object is present (matches the original), so a campaign-context
  value can still fill an unresolved field.
- `src/app/api/routes/automation_workflows.py`: added `MergeFieldResponse` and
  `GET /automation/workflows/merge-fields` returning the catalog (with a ready
  `token` = `{{name}}`). **Declared before `/{workflow_id}`** so the literal path
  is not captured as a workflow id (the shadowing trap flagged in findings §4).

### Tests (3 new) — `tests/unit/test_automation_workflow_routes.py`
- `test_list_merge_fields_returns_catalog_with_tokens` — endpoint returns the 4
  fields with correct `{{token}}`.
- `test_merge_field_catalog_does_not_drift_from_renderer` — renders a template of
  every catalog token and asserts no raw `{{...}}` remain (drift guard).
- `test_merge_fields_route_declared_before_workflow_id` — route-ordering guard.

### Validation
- `pytest` automation route/template/executor suites → **91 passed**.
- `ruff check` on the two source files → **All checks passed** (the 6 F401s remain
  pre-existing unused imports in the test file; my new imports are all used).

**Result:** #3 delivered and green. Remaining deferred: #4, #5 (dry-run half),
#7, T1, T2 — blockers unchanged (voice stub / Plan-06 runtime / product+schema
decisions).
