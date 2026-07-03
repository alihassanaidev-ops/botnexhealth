# Progress: Outbound 02 — Visual Workflow Builder UI

## Session start — 2026-07-03
- **Status:** research + planning complete; implementation starting.
- Graph refreshed (`graphify update .`): 5810 nodes / 17892 edges / 229 communities, no topology drift.
- Research: 3 parallel graph-oriented passes (frontend architecture, UI idioms, backend contract) + direct reads of `Campaigns.tsx`, `CampaignDetail.tsx`, `automation-api.ts`, `types/index.ts`, `package.json`, `vite.config.ts`, sample tests. Full record in `findings.md`.
- Plan authored + self-validated (see "Plan validation" below). 9 phases, ~28 files.

### Plan validation (self-review before coding)
- ✅ Route nesting under `/institution-admin/campaigns/*` confirmed non-colliding (RRv7 ranks static `templates` above `:id`).
- ✅ `extra="forbid"` on the definition schema confirmed → layout must be derived, not persisted. Plan reflects this (A4).
- ✅ Clone-via-create avoids the broken `instantiate` (TPL-01/02) — verified create endpoint accepts `{name, definition}` and publishes.
- ✅ RHF+zod, Sheet, Dialog, sonner, `cn`, `STATUS_STYLES`, Skeleton/empty-state idioms all confirmed present and mirrored.
- ✅ Test stack (Vitest+RTL+jsdom, `src/test/`, `vi.mock` api/token/sonner) confirmed; test plan matches.
- ✅ `@xyflow/react` React-19 compatible (v12 supports React 17–19).
- ⚠️ Recorded limitations (backend follow-ups): version-list, backend validate payloads, merge-field catalog, channel-readiness, non-destructive test-run — all implemented client-side or degrade gracefully (findings.md §5).

---

## Slice log

### Slice 1 — Foundation (Phases 0–2) — complete
- **Phase 0:** installed `@xyflow/react@12.11.1`; baseline `npm run build` ✓ and `npm test` (33/33) ✓ before any changes.
- **Phase 1 (pure lib):** `types/workflow.ts` (exact mirror of `definition_schema.py`), `lib/workflow/{graph,validation,merge-fields,preview,test-run,catalog}.ts`.
  - `graph.ts`: definition↔React Flow derivation, deterministic layered layout (BFS depth from trigger; unreachable nodes get a trailing column), pointer helpers, node/trigger factories, immutable add/update/remove (remove bypasses linear predecessors), `blankDefinition`, `serializeDefinition`. Layout NOT persisted (schema `extra="forbid"`).
  - `validation.ts`: node-linked `ValidationIssue[]` mirroring + extending backend `validate_graph_structure` (dup ids, empty content, attempts range, unreachable, self-loop, unknown merge tokens); `isPublishable`, `unreachableNodes`.
  - `merge-fields.ts`/`preview.ts`: client-side catalog + `{{token}}` rendering + SMS segment estimate.
  - `test-run.ts`: client-side dry-run walker (cycle guard @50 steps, condition-choice override, dead-end detection).
  - `catalog.ts`: palette/node display metadata (labels, lucide icons, accents, groups).
- **Phase 2 (API client):** `lib/workflow-api.ts` — mirrors `automation-api.ts` idiom. Clone flow uses `getTemplate`→`createWorkflow` to sidestep the broken `instantiate` (TPL-01/02); documented + switchable when fixed.
- **Tests:** 5 new suites, **50 new tests** (graph, validation, preview, test-run, api) — all green. Full suite now 83 tests.
- **Fixes during slice:** removed unused `ConditionNode` import (TS6133); replaced `.at(-1)` with index access (tsconfig target < ES2022). `tsc -b` exits 0.
- **Decision:** added optional `issueLevel` overlay to `FlowNodeData` so the canvas can tint nodes with validation state without re-running layout.

### Slice 2 — Canvas, config, pages & wiring (Phases 3–8) — complete
**Components (`src/components/workflow/`):** `WorkflowCanvas` (React Flow wrapper: pan/zoom, selection, validation tinting, non-draggable/non-connectable — edges authored via forms), `WorkflowNode` (trigger + step renderers with type icons/summaries/handles), `WorkflowPalette` (grouped click-to-add + trigger affordance), `StepConfigPanel` (right Sheet; controlled typed forms per node/trigger type incl. condition rule editor, next-step selectors, merge-field insert, quiet-hours, max-attempts), `WorkflowValidationPanel` (node-linked issues, click→select), `MessagePreview` (SMS bubble + email card with sample merge data), `TestRunDialog` (dry-run with per-condition branch toggles), `WorkflowPublishControls` (test/discard/pause/resume/archive/publish with explicit confirm dialogs).

**Pages (`src/pages/`):** `WorkflowTemplates` (picker + clone→builder), `WorkflowBuilder` (flagship: palette | canvas | validation rail + config Sheet + test dialog; client-side draft w/ localStorage autosave + discard; publish = PATCH), `WorkflowVersions` (read-only current-snapshot canvas).

**Wiring (minimal, additive):** `router.tsx` (+3 lazy routes: templates, `:id/builder`, `:id/versions`, all `INSTITUTION_ADMIN`, nested under existing `/institution-admin/campaigns`), `Campaigns.tsx` (+"New from template" header button, +per-row "Edit in builder" link), `package.json` (+`@xyflow/react`), `src/test/setup.ts` (+ResizeObserver/matchMedia/DOMMatrix jsdom stubs for canvas tests).

**Fixes during slice:**
- React Flow v12 requires node `data extends Record<string,unknown>` → rewrote `FlowNodeData` as type-alias union (interfaces lack the implicit index signature).
- react-refresh lint: moved the `nodeTypes` map out of `WorkflowNode.tsx` (export only components) into `WorkflowCanvas.tsx`; un-exported internal summary helpers.
- Removed unused `publishWorkflow`/`useNavigate` imports.

**Validation results (Phase 7):**
- `npx tsc -b` → exit 0.
- `npm run lint` → 0 errors (1 pre-existing warning in `CampaignDetail.tsx`, not ours).
- `npm test` → **15 files / 92 tests pass** (33 baseline + 59 new: graph, validation, preview, test-run, api, templates page, validation panel, builder smoke).
- `npm run build` (`tsc -b && vite build`) → exit 0. Builder + canvas code-split into lazy chunks (React Flow ~178 kB loads only on the builder route).

**Manual test checklist (for a reviewer with the app + backend running):**
1. Campaigns → "New from template" → pick Appointment Reminder → name → Create → lands in builder with the graph rendered.
2. Click SMS node → edit body (insert `{{patient_first_name}}`) → live preview + segment count update.
3. Add a Condition from the palette → set Yes/No next steps via selectors → edges appear.
4. Introduce an error (clear a message / disconnect a branch) → node tints red, validation rail lists it, Publish disabled; click issue → selects node.
5. Test run → step list + branch toggles + final outcome; no real sends.
6. Publish changes → confirm dialog → PATCH → status/toast; refresh persists.
7. Pause/Resume/Archive; Versions page shows the published snapshot read-only.
8. Refresh mid-edit → "Restored unsaved changes"; Discard → reverts to published.

**Architecture decisions recorded:** see `task_plan.md` §1 (A1–A9) and `findings.md` §4. Key: no backend changes (frontend-isolated); layout derived not persisted (schema `extra="forbid"`); clone via create (broken `instantiate` avoided); client-side validation/preview/test-run; nested under existing campaigns route.

**Limitations / backend follow-ups (documented, non-blocking):** full version-list + visual diff, backend node-linked validate endpoint, merge-field catalog endpoint, per-channel readiness endpoint, non-destructive server test-run, and fixing `instantiate` (TPL-01/02) — all owned by Plans 01/06/10. UI degrades gracefully and is wired to switch over when they land. Concurrent-edit is last-write-wins (no ETag) — noted for a future optimistic-lock. See `findings.md` §5.

### Session outcome
Builder UI delivered **complete and green** (tsc/lint/test/build), as a natural extension of the existing dashboard (same shell, pills, forms, Sheet/Dialog, toasts, icons, route namespace). No TODOs left in shipped code. Graph refreshed. **Status: DONE.**
