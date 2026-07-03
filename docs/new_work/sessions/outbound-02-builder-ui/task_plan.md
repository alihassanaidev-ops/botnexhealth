# Task Plan: Outbound 02 — Visual Workflow Builder UI

> Single end-to-end session: research → plan → implement → validate → test.
> **Mode:** Auto / auto-approved. Deliver a **complete** implementation (no TODOs/deferrals).
> Frontend-only, isolated lane — **no backend files touched** (see `findings.md` §4).
> Branch: `feature/outbound-engagement-engine` @ `5563b57`.

## Goal
Ship a GoHighLevel-style visual workflow builder in the existing dashboard that lets an
INSTITUTION_ADMIN clone a launch-campaign template, visually view/edit the workflow graph,
configure each step via typed forms, validate against the Plan-01 schema with node-linked
errors, preview messages, dry-run, and publish/pause/archive/version — feeling like a native
extension of the app.

## 1. Architecture decisions (finalized — see findings.md §3–§4 for basis)
- **A1** Nest routes under `/institution-admin/campaigns/*` (not a new `/campaigns` root). Reuses nav + avoids Plan-08 collision.
- **A2** No backend changes. Missing endpoints → client-side (validation, preview, test-run, merge-fields) or documented dependency (version list, channel readiness).
- **A3** Clone via `POST /automation/workflows {name, definition}` (working) instead of broken `instantiate`.
- **A4** Graph layout derived client-side (deterministic layered layout); never persisted (schema `extra="forbid"`).
- **A5** Editing buffer = client-side draft (state + localStorage autosave). Publish = POST (new) / PATCH (existing) with explicit confirmation Dialog. No server draft-with-definition exists.
- **A6** RBAC `INSTITUTION_ADMIN` (matches backend + existing campaigns).
- **A7** Add `@xyflow/react` (React Flow v12, React-19 compatible) for the canvas.
- **A8** Reuse all existing idioms: page shell, `STATUS_STYLES` pills, RHF+zod forms, `Sheet` config panel, `Dialog` confirms, `sonner` toasts, `cn()`/cva, lucide icons, Skeleton/empty states.
- **A9** New code lives under `src/components/workflow/`, `src/lib/workflow/`, `src/pages/`, `src/types/workflow.ts` — additive; the only edits to existing files are: `router.tsx` (add routes), `app-sidebar.tsx` (add a "Templates" nav entry, optional), `pages/Campaigns.tsx` (minimal, additive: "New from template" + per-row "Edit in builder"), `package.json` (+react-flow).

## 2. File manifest (new unless noted)
**Types & lib (pure, testable):**
- `src/types/workflow.ts` — typed `WorkflowDefinition`, triggers, nodes, delays, condition rules, `ValidationIssue`, `MergeField`, `TestRunResult`.
- `src/lib/workflow-api.ts` — API client (list/get/create/update/publish/pause/resume/archive workflows; list/get templates; createFromTemplate).
- `src/lib/workflow/graph.ts` — definition ↔ React Flow (nodes/edges) derivation + deterministic layered layout + node mutation helpers (add/update/delete/rewire) + serialize.
- `src/lib/workflow/validation.ts` — client-side validator → `ValidationIssue[]` (node-linked; mirrors + extends `validate_graph_structure`).
- `src/lib/workflow/merge-fields.ts` — merge-field catalog + sample data.
- `src/lib/workflow/preview.ts` — render body/subject templates with sample merge data.
- `src/lib/workflow/test-run.ts` — client-side dry-run simulation (path + would-send).
- `src/lib/workflow/catalog.ts` — palette catalog (node/trigger metadata: label, icon, channel/group).

**Components (`src/components/workflow/`):**
- `WorkflowCanvas.tsx` — React Flow wrapper (provider, nodes/edges, pan/zoom, selection, read-only mode).
- `WorkflowNode.tsx` — custom node renderer (variants: trigger, wait, condition, send_sms, send_voice, send_email, exit).
- `WorkflowPalette.tsx` — add-node side palette grouped by channel/control-flow.
- `StepConfigPanel.tsx` — right `Sheet`; RHF+zod typed form per node/trigger type.
- `WorkflowValidationPanel.tsx` — node-linked errors/warnings; click → select node.
- `WorkflowPublishControls.tsx` — save/validate/publish/pause/resume/archive + confirm Dialog.
- `MessagePreview.tsx` — SMS/email preview with sample merge data.
- `TestRunDialog.tsx` — dry-run simulation results.

**Pages (`src/pages/`):**
- `WorkflowTemplates.tsx` — template picker + clone flow (`/institution-admin/campaigns/templates`).
- `WorkflowBuilder.tsx` — the builder (`/institution-admin/campaigns/:id/builder`).
- `WorkflowVersions.tsx` — current published snapshot viewer (`/institution-admin/campaigns/:id/versions`).

**Edits to existing files (additive, minimal):**
- `src/router.tsx` — 3 lazy routes (templates, builder, versions).
- `src/components/app-sidebar.tsx` — no new top-level entry required (existing Campaigns highlights nested routes); add nothing OR a Settings-group "Workflow Templates" link if discovery needs it. Decide during impl; keep minimal.
- `src/pages/Campaigns.tsx` — header "New from template" button + per-row builder link.
- `nexus-dashboard-web/package.json` + lockfile — add `@xyflow/react`.

**Tests (`src/test/`):**
- `workflow-graph.test.ts`, `workflow-validation.test.ts`, `workflow-preview.test.ts`, `workflow-test-run.test.ts`, `workflow-api.test.ts` (mock axios), `WorkflowTemplates.test.tsx`, `WorkflowBuilder.render.test.tsx` (RTL, mocked api).

## 3. Phases (status tracked here; detail in progress.md)

- **Phase 0 — Setup & dependency.** Add `@xyflow/react`, verify install + baseline `npm run build`/`test` still green. **Status:** complete (build ✓, 33/33 tests ✓, `@xyflow/react@12.11.1`)
- **Phase 1 — Types & pure lib.** `types/workflow.ts`, `graph.ts`, `validation.ts`, `merge-fields.ts`, `preview.ts`, `test-run.ts`, `catalog.ts`. Unit-test each. **Status:** complete (tsc ✓)
- **Phase 2 — API client.** `workflow-api.ts` + test. **Status:** complete (tsc ✓)
- **Phase 3 — Canvas & nodes.** `WorkflowCanvas`, `WorkflowNode`. **Status:** complete (tsc ✓; React Flow v12 data-type constraint resolved)
- **Phase 4 — Config & palette.** `StepConfigPanel` (typed forms), `WorkflowPalette`. **Status:** complete (tsc ✓)
- **Phase 5 — Validation, preview, test-run.** `WorkflowValidationPanel`, `MessagePreview`, `TestRunDialog`. **Status:** complete (tsc ✓)
- **Phase 6 — Pages & routing.** `WorkflowTemplates`, `WorkflowBuilder`, `WorkflowVersions`, `WorkflowPublishControls`; wired `router.tsx`, `Campaigns.tsx`. Sidebar left unchanged (existing Campaigns entry highlights nested routes). **Status:** complete (tsc ✓)
- **Phase 7 — Validate & test.** `tsc -b` ✓, `vite build` ✓, `eslint` ✓ (0 errors), `vitest run` ✓ (92/92). Component + smoke tests added. **Status:** complete
- **Phase 8 — Graph update & wrap-up.** `graphify update .` ✓ (5999 nodes). Session docs finalized. **Status:** complete

## 4. Definition of done
- All phases complete; `npm run build` (tsc + vite), `npm run lint`, `npm test` all green.
- Clone→view→edit→configure→validate→preview→test-run→publish→pause/archive→versions works against the real backend contract (verified by tests + documented manual steps).
- No TODOs/stubs in shipped code. Every limitation recorded in `findings.md` §5 / `progress.md`.
- Feels native: reuses existing shell, pills, forms, sheet/dialog, toasts, icons.

## Open questions (resolved for v1)
- All schema nodes supported? → **Yes** (wait, send_sms, send_voice, send_email, condition, exit) + all 4 trigger types. Full parity with Plan-01 schema.
- Edit templates through builder? → Clone template → edit the resulting workflow. Templates themselves are read-only system definitions.
- Save = draft or publish? → See A5: explicit Publish (POST/PATCH) with confirmation; editing buffer is client-side.
