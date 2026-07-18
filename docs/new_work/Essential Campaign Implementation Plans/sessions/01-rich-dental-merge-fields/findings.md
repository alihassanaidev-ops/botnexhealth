# Findings And Decisions

## Requirements

- Backend catalog must expose static and dental context-backed merge fields.
- API must support trigger/channel filters while preserving current token behavior.
- Validation must warn or block unavailable merge fields before launch/publish.
- Final rendering must avoid raw `{{token}}` text reaching patients.
- Frontend picker and preview must use grouped/filterable catalog metadata.
- Tests should be added where behavior changes, both backend and frontend.

## Research Findings

- Existing backend entry point named in plan: `src/app/services/automation/template_renderer.py`.
- Existing frontend entry point named in plan: `nexus-dashboard-web/src/lib/workflow/merge-fields.ts`.
- Repository instructs codebase questions to start with `graphify query` when `graphify-out/graph.json` exists.
- No `.cloud` directory exists; `.claude/planning-with-files/templates` contains `task_plan.md`, `progress.md`, and `findings.md` templates.
- Backend trigger types are `appointment_offset`, `recall_scan`, `manual`, `bulk_import`, and `callback_requested`; frontend was missing `callback_requested`.
- Existing default campaign templates do not include appointment date/time or booking/confirmation link tokens.
- Per-run booking/confirmation/reschedule link generation is not implemented in the current workflow runtime.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Keep per-plan session files under the plans directory | Keeps implementation context close to the source plans. |
| Do not add link tokens to shipped templates until link generation exists | A default template containing `{{confirmation_link}}` would render blank in live sends until link generation exists. |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| User referenced `.cloud`, but checkout contains `.claude` | Proceeded with `.claude/planning-with-files/templates` because it matches the described template role. |
| Default templates with link tokens are ambiguous before link generation | User approved deferring link tokens until link generation exists. |

## Resources

- docs/new_work/Essential Campaign Implementation Plans/01-rich-dental-merge-fields.md
- docs/new_work/Essential Campaign Implementation Plans/11-concrete-campaign-build-plan.md
- docs/new_work/Essential Campaign Implementation Plans/12-campaign-implementation-decisions.md
- .claude/planning-with-files/templates
