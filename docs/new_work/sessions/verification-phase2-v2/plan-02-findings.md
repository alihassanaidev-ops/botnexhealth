# Plan 02 — Visual Workflow Builder UI — Verification Findings

Audited: 2026-07-03. Branch `ali/phase-2`. Evidence-based against actual code.
Plan: `docs/new_work/Implementation Plans/02-visual-workflow-builder-ui.md`
Sessions: `outbound-02-builder-ui` (frontend), `outbound-03-builder-backend-followups` (backend).
Scope ref: `Outbound_Engagement_Engine_Scope.md` §9.1.

## Verdict
**Substantially complete (~75%).** The core no-code builder is a *real* React Flow
canvas — not placeholder forms — with palette, typed per-step config, visual
branching/waits, node-linked validation, lifecycle controls, template clone,
message preview, and a client-side dry-run. Confidence: High.

The gaps are (a) compliance guardrails that the plan/scope explicitly require, and
(b) a frontend↔backend integration lag: Plan 03 shipped `/validate`, `/versions`,
`/merge-fields` endpoints that the frontend never wired up.

---

## Is it a real flow-canvas? YES
- `@xyflow/react@12.11.1` is a real dependency (`nexus-dashboard-web/package.json:29`).
- `WorkflowCanvas.tsx` renders `<ReactFlow>` with pan/zoom, `<Controls>`, `<MiniMap>`,
  dotted `<Background>`, custom `nodeTypes` (trigger/step), node selection, validation
  tinting (`WorkflowCanvas.tsx:48-75`). Code-split into a lazy chunk.
- Custom node renderers `WorkflowNode.tsx` (StepNodeCard/TriggerNodeCard, 127 lines).
- **Not** drag-authored: `nodesDraggable={false} nodesConnectable={false}`
  (`WorkflowCanvas.tsx:55-56`). Layout is *derived* deterministically (layered BFS from
  trigger) in `graph.ts:101-191` and NOT persisted (backend schema is `extra="forbid"`).
  Edges are authored via next-step `<Select>`s in the config panel, not by dragging.
  → Scope §9.1 permits "drag-and-drop **or** click-to-add", so this is in-scope, but it
  is not the full drag experience the plan headline ("GoHighLevel-style") implies. Plan
  itself lists drag-and-drop under "Future Extensibility".

## Completed (with evidence)
- **Routes** `router.tsx:44-46, 272-307`: `/institution-admin/campaigns/templates`,
  `/:id/builder`, `/:id/versions`, all lazy + `INSTITUTION_ADMIN` role-gated, nested under
  existing campaigns route.
- **Palette** `WorkflowPalette.tsx` — grouped click-to-add (`PALETTE_GROUPS` from
  `catalog.ts`) + trigger affordance.
- **Per-step config** `StepConfigPanel.tsx` (629 lines) — typed controlled forms for all
  6 node types + trigger: SMS/email/voice, wait (duration + calendar), condition rule
  editor with AND/OR + operators (`ConditionFields`, :376-481), next-step selectors
  (`NextStepField`, :552-587), quiet-hours switch, max-attempts (1–3), merge-field insert
  menu. Never raw JSON.
- **Visual branching + waits** — condition emits `true`/`false` handles with "Yes"/"No"
  edge labels (`graph.ts:60-75, 177-188`); wait nodes first-class.
- **Client-side validation** `validation.ts` (265 lines) — node-linked `ValidationIssue[]`:
  entry/exit presence, dangling/missing pointers, empty content, attempts range,
  duplicate ids, self-loops, unreachable nodes, HH:MM, unknown merge tokens. Surfaced in
  `WorkflowValidationPanel.tsx` with click-to-select-node. Publish blocked on errorCount
  (`WorkflowBuilder.tsx:208-213`).
- **Lifecycle controls** `WorkflowPublishControls.tsx` — publish/discard/pause/resume/
  archive with confirm dialogs; wired in `WorkflowBuilder.tsx:286-297`.
- **Template start** `WorkflowTemplates.tsx` — lists templates, clone→builder via
  `createWorkflowFromTemplate` (`workflow-api.ts:90-99`).
- **Preview** `MessagePreview.tsx` (SMS bubble + email card, sample merge data);
  **Test-run** `TestRunDialog.tsx` client-side walker with per-condition branch toggles,
  50-step loop guard.
- **Version viewer** `WorkflowVersions.tsx` — read-only current-snapshot canvas.
- **API client** `workflow-api.ts` — list/get/create/update/publish/pause/resume/archive/
  templates.
- **Backend follow-ups (Plan 03)** in `automation_workflows.py`:
  - `POST /automation/workflows/validate` (:238-255) — authoritative pydantic validation,
    node-linked issues via `_node_id_for_loc`/`_issue_from_pydantic_error` (:188-217).
  - `GET /automation/workflows/{id}/versions` (:306-327) — newest-first, `is_current`.
  - `GET /automation/workflows/merge-fields` (:267-291) — catalog from renderer's
    `STATIC_MERGE_FIELDS`, declared before `/{workflow_id}` to avoid path shadowing.
  - `instantiate` fix (in `automation_templates.py`, per session docs).
  - `template_renderer.py` — `STATIC_MERGE_FIELDS` single-source for renderer + catalog.

## Missing / bugs
1. **Frontend never consumes the Plan-03 endpoints (integration debt).**
   `workflow-api.ts` has NO `listVersions`, `validateDefinition`, or `listMergeFields`.
   - `WorkflowVersions.tsx:3-8, 80-86` still asserts "there is no version-list endpoint"
     and shows only the current version — but `GET /{id}/versions` exists and returns full
     history. The page is factually stale.
   - `merge-fields.ts:1-9` still asserts "there is no backend merge-field catalog endpoint"
     — but `/merge-fields` exists.
   - Publish relies purely on client-side `validateDefinition`; the authoritative
     `/validate` endpoint is never called (only a 422 on publish surfaces backend rules).
   Two sessions were done in isolation and the frontend was never rewired.
2. **Merge-field drift — the exact failure Plan 03 tried to prevent.**
   Frontend `MERGE_FIELDS` (`merge-fields.ts:12-19`) advertises 6 tokens incl.
   `provider_name`, `appointment_date`, `appointment_time`. Backend `STATIC_MERGE_FIELDS`
   (`template_renderer.py:54-91`) has only 4: `patient_first_name`, `patient_last_name`,
   `patient_full_name`, `clinic_name`. So the builder shows/validates 3 tokens as "known"
   that the renderer substitutes to empty string (unless supplied via dynamic context).
   Concrete correctness gap; caused directly by #1 (frontend uses its own static list).
3. **Compliance / Part 12 guardrails absent.** Plan requires `WorkflowValidationPanel` to
   surface content-class violations, PHI-in-body warnings, missing consent path, and
   blast-radius warnings (plan :80-83). Scope §9.1 requires the "no consent path"
   guardrail. `validation.ts` is purely structural — NONE of these exist anywhere
   (frontend or the `/validate` backend). This is an explicit plan requirement not met.
4. **Channel readiness not surfaced.** No Twilio/email/Retell provisioning check before
   publish (plan "Backend APIs Needed" + Technical Considerations). Deferred #4.
5. **No server-side test-run.** `TestRunDialog` is a client-only walker; there is no
   dry-run against a real sample contact/appointment/recall context via backend. Plan
   "Preview & test … test-run against sample contact" only half met.
6. **No true server-side draft.** `update_workflow` → `publish_version` makes the workflow
   ACTIVE immediately (`automation_workflows.py:330-346`). The "draft" is a client
   `localStorage` buffer only (`WorkflowBuilder.tsx:55, 82-95, 113`). The plan edge case
   "active published version AND editable draft at the same time" is not truly supported
   at the backend (deferred #7). Editing then publishing an active workflow silently
   changes live behavior for new runs.

## Architectural concerns
- **Two validators to keep in sync.** `validation.ts` (client) re-implements
  `WorkflowDefinition.validate_graph_structure` (backend). They can drift; the
  authoritative `/validate` was built to avoid this but is not called. Drift already
  exists in merge fields (#2).
- **Publish mutates live behavior with no blast-radius gate.** Publishing an edit to an
  ACTIVE campaign creates a new active version with only a generic confirm dialog — no
  step-up approval for large enrollment/spend (Part 12), which the plan's validation panel
  was meant to enforce.
- **Concurrency = last-write-wins.** No ETag/optimistic lock (T1 deferred); plan edge case
  "concurrent edit overwrites another user's draft" unaddressed.

## Technical debt
- Stale limitation comments now factually wrong: `WorkflowVersions.tsx:3-8, 83-85`,
  `merge-fields.ts:1-9`.
- 6 pre-existing F401 unused imports in `tests/unit/test_automation_workflow_routes.py`
  (WorkflowUpdateRequest, _get_workflow_or_404, archive/pause/resume/update_workflow) —
  acknowledged in session, left untouched.

## Code quality
Generally high. Clean separation into pure lib modules (`graph`, `validation`,
`merge-fields`, `preview`, `test-run`, `catalog`); TS discriminated-union node model
mirrors backend `definition_schema.py`; immutable mutation helpers; deterministic layout;
good docstrings; consistent reuse of shadcn primitives, sonner, `cn`, STATUS_STYLES. The
builder is code-split so React Flow loads only on the builder route.

## Tests
- **Backend:** `tests/unit/test_automation_workflow_routes.py` — **24 passed** (verified
  run). Covers validate (valid/missing-exit/node-linking), versions (ordering + 404),
  merge-fields (catalog, drift-guard, route-ordering), CRUD, enroll, runs.
- **Frontend:** all workflow suites green — `workflow-graph`, `workflow-validation`,
  `workflow-preview`, `workflow-test-run`, `workflow-api`, `WorkflowTemplates`,
  `WorkflowValidationPanel`, `WorkflowBuilder.render`. Full run **91/92** — the single
  failure is `Security.test.tsx` (MFA page, unrelated to Plan 02).
- **Coverage gaps:** tests exercise client-side libs + render smoke but NOT the
  backend-endpoint integration (because it isn't wired) and have NO compliance-validation
  tests (feature absent).

## Scope alignment
Core §9.1 flagship experience is genuinely delivered (canvas, palette, per-step config,
visual branch/wait, draft/publish/pause + version viewer, template start, preview,
test-run). Two scope items are not met: **compliance guardrails (no-consent-path etc.)**
and the **integration of the authoritative backend validate/versions/merge-field
endpoints** — both built but unconsumed, plus a live merge-field drift bug.
