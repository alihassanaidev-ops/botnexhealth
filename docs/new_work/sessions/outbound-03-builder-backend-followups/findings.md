# Findings: Outbound 03 — Builder Backend Follow-ups

> Investigation record for the backend follow-ups deferred by `outbound-02-builder-ui`.
> Compiled 2026-07-03 from first-hand reads of the automation subsystem at HEAD
> `5563b57`. Each verdict is grounded in a specific file/line observation.

---

## 1. Files investigated
- `src/app/api/routes/automation_workflows.py` — workflow CRUD + lifecycle routes.
- `src/app/api/routes/automation_templates.py` — template list/get/instantiate.
- `src/app/services/automation/definition_service.py` — create_draft / publish_version / lifecycle.
- `src/app/services/automation/definition_schema.py` — `WorkflowDefinition` + `validate_graph_structure`.
- `src/app/services/automation/campaign_templates.py` — the 4 seed templates.
- `src/app/services/automation/step_dispatcher.py` — run advancement + **send stubs**.
- `src/app/models/automation_workflow.py` — workflow + version + run models.
- `tests/unit/test_automation_workflow_routes.py`, `tests/unit/test_automation_campaign_templates.py` — test idioms.

---

## 2. Key structural facts (drive every verdict)

### 2.1 A definition only ever lives inside a published version
- `AutomationWorkflow.definition` is a **read-only property** → `current_version.definition` (`automation_workflow.py:144`).
- The only writer of a version row is `publish_version()`, which **always sets `status = ACTIVE`** (`definition_service.py:180`).
- `create_draft()` makes an **empty** DRAFT with no version and **accepts neither `trigger_type` nor `definition`** (`definition_service.py:38-66`).
- ⇒ There is **no way to hold a definition while `status = draft`.** This single fact is the root cause of findings #1 and #7.

### 2.2 `create_workflow` is the canonical "author a definition" path
`POST /automation/workflows` = `create_draft(name)` → `publish_version(definition)` → **active** (`automation_workflows.py:137-147`). The frontend clone flow already uses this, which is why it could route around the broken `instantiate`.

### 2.3 Validation already exists server-side
`WorkflowDefinition.model_validate()` runs `validate_graph_structure` (a `model_validator(mode="after")`) which enforces entry-node existence, next/branch ref integrity, and ≥1 exit node (`definition_schema.py:191-219`). Publish surfaces failures as a 422 (`definition_service.py:143-150`). Nothing exposes this *before* publish.

### 2.4 There is NO merge-field renderer (critical)
`step_dispatcher._dispatch_send_stub` (`step_dispatcher.py:217-229`) records intent and returns `next_node_id` **without rendering `body_template`/`subject_template`**. Module docstring: *"send_* nodes are stubbed here; real action handlers are registered in Plans 03/04/05."* `{{patient_first_name}}` / `{{clinic_name}}` exist only as literal strings in `campaign_templates.py`. **The authoritative merge-field set is undefined until the send handlers land.**

### 2.5 Version history is fully modeled, just unexposed
`AutomationWorkflowVersion` stores `version_number` (unique per workflow), `definition`, `definition_checksum`, `content_classification`, `published_by_user_id`, `published_at`, `created_at`. `AutomationWorkflow.versions` is a `lazy="selectin"` relationship (eager-loaded). Only `current_version_id` was reachable via the API.

---

## 3. Per-finding investigation & verdict

### #1 — `instantiate` broken (IMPLEMENTED)
`automation_templates.py` called `create_draft(institution_id=…, name=…, trigger_type=…, definition=…)` but `create_draft` accepts neither `trigger_type` nor `definition` → **`TypeError` on every call**. Even with those removed it makes an empty draft (definition dropped, §2.1).
- **Why it slipped through:** the existing test mocked `create_draft` as a loose `AsyncMock`, so the bad kwargs were silently accepted and the crash never reproduced in CI.
- **Fix:** mirror `create_workflow` — `create_draft(name)` then `publish_version(template.definition)`, returning `WorkflowResponse`. Result is an **active** workflow (§2.1/§2.2 — no draft-with-definition exists). True "draft from template" is gated on #7; documented in the route docstring.

### #2 — Validate endpoint (IMPLEMENTED)
Exposed the existing validator pre-publish: `POST /automation/workflows/validate` → `{valid, issues[]}`. Reuses `WorkflowDefinition.model_validate`; translates `ValidationError.errors()` into `ValidationIssueResponse{severity, node_id, field_path, message}`.
- **Node-linking:** pydantic field errors carry a loc like `("nodes", <index>, …)`; `_node_id_for_loc` maps the positional index back to the node's declared `id`. Graph-structure `ValueError`s (loc `()`) return `node_id=null` with the raw message (the "Value error, " prefix is stripped).
- No dependency: pure schema validation, no DB, no runtime.

### #6 — Version-list (IMPLEMENTED)
`GET /automation/workflows/{id}/versions` → newest-first `WorkflowVersionResponse[]` with an `is_current` flag. Reuses `_get_workflow_or_404` (ownership/404) and the already-eager `wf.versions`. Zero new data access — the model had everything (§2.5).

### #3 — Merge-field catalog (DEFERRED — blocked)
The authoritative field set is defined by the send handlers, which **do not exist** (§2.4, Plans 03/04/05). Shipping a server catalog now would invent a contract the engine does not honor and risk drift. **No regression:** the frontend already ships a client-side catalog. Revisit when Plans 03/04/05 land the renderer.

### #4 — Per-channel readiness (DEFERRED — partially blocked + unspecified)
- **Voice** readiness needs a per-clinic **Retell agent id**; that provisioning does not exist (it's why `send_voice` is omitted from every template — `campaign_templates.py:1-7`).
- **SMS/email** columns exist (Plan 10, commit `5563b57`: `twilio_account_sid_encrypted`, `email_from_address`), so a partial endpoint is *technically* buildable, but the "ready" contract (what counts as ready per channel, response shape) is unspecified, and the frontend already degrades gracefully via emergency-halt + client-side per-node completeness. Deferred pending a readiness contract + voice provisioning.

### #5 — Server test-run / dry-run (DEFERRED — double-blocked)
(a) A faithful simulation must reuse Plan-06 runtime branch/quiet-hours/compliance logic (`step_dispatcher.py`); (b) a "would-send" preview can't be authoritative because the send-handler renderer is a stub (§2.4, same blocker as #3). The frontend client-side simulator is an adequate stand-in until Plan 06 exposes a no-dispatch runtime mode.

### #7 — Draft-with-definition (DEFERRED — design decision + migration)
Root structural gap (§2.1): definitions live only in published versions and publishing forces ACTIVE. Supporting real drafts needs either (a) decoupling version creation from activation, or (b) a `draft_definition` JSONB column on `AutomationWorkflow` — both are **product decisions + a schema migration**, not mechanical fixes. This also gates a "true draft" `instantiate`.

### T1 — Optimistic lock / ETag (DEFERRED — needs decision + FE coordination)
Requires a concurrency contract (which token — `updated_at`/version — and which status: 409 vs 412) and client cooperation (the frontend does not send a version token today). Server-only enforcement would either do nothing or break existing PATCH. Deferred.

### T2 — `send_voice` template seed (DEFERRED) — same blocker as #4 (needs a clinic Retell agent id).

---

## 4. Bonus observation (not in scope — flagged for follow-up)
`GET /automation/workflows/outbound-halt` is declared **after** `GET /{workflow_id}` (`automation_workflows.py`). Starlette matches routes in declaration order; a single-segment literal declared after a `/{param}` route can be shadowed (captured as `workflow_id="outbound-halt"`). **Unverified** — needs a routing test. My two new routes avoid this: `/validate` is POST (no GET `/{param}` collision) and `/{id}/versions` has an extra segment (unambiguous). Recommend a dedicated test for the halt routes.

## 5. Net result
- **3 implemented, unblocked:** #1, #2, #6.
- **6 deferred with concrete blockers:** #3, #4, #5, #7, T1, T2 — every one traces to a missing component (send-handler renderer, Retell provisioning, Plan-06 runtime) or an unresolved product/schema decision, not to effort.
