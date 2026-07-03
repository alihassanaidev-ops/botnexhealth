# Part 2 - Visual Workflow Builder UI Implementation Plan

## What Needs To Be Built

Build the dashboard UI for authoring, configuring, validating, publishing, pausing, duplicating, and versioning workflows. The primary experience is a GoHighLevel-style no-code canvas with a side palette, visual branches/waits/actions, per-step configuration panels, validation guardrails, preview/test tooling, and draft/publish/version controls.

The launch UI should support clinic-facing template configuration and guided visual customization. Full arbitrary authoring can be operator-only initially, but the UI architecture should not block future clinic-facing free-form authoring.

## Existing System Context

The frontend already has:

- Vite + React 19 + TypeScript.
- React Router role-gated routes in `nexus-dashboard-web/src/router.tsx`.
- Shared layout/sidebar components.
- shadcn/Tailwind-style UI primitives under `components/ui`.
- Existing form/dialog/table/tabs/sheet/select/textarea/toast patterns.
- Institution and location context providers.
- SSE hook for live updates.
- Existing pages for dashboard, calls, callbacks, setup, tenants, email templates, workflow statuses, and admin areas.
- Role guards for `SUPER_ADMIN`, `INSTITUTION_ADMIN`, `LOCATION_ADMIN`, `STAFF`, and group oversight.

Current gaps:

- No node graph or flow canvas UI exists.
- No workflow API client exists.
- No campaign/workflow pages exist.
- No visual validation/publish flow exists.
- No template cloning or workflow version UI exists.

## Existing Components To Reuse

- `RoleGuard`, `PmsGuard`, `DashboardWrapper`, `AppLayout`, and sidebar conventions.
- `LocationContext` and location selector behavior for location-scoped editing.
- Existing `api.ts`/feature API wrapper style.
- UI primitives: button, sheet, dialog, form, tabs, table, badge, switch, select, textarea, tooltip.
- Existing email template preview/editing conventions where useful.
- Existing SSE hook for progress refresh hints after publish/run changes.

## New Components Required

### Frontend Routes And Pages

- `/campaigns`
  - campaign/workflow list and template entry point

- `/campaigns/:workflowId`
  - campaign detail and configuration overview

- `/campaigns/:workflowId/builder`
  - visual workflow builder canvas

- `/campaigns/:workflowId/versions`
  - version history and published snapshot viewer

- `/campaigns/templates`
  - system templates and clone flow

Routes should be role-gated to institution/location admins for clinic-facing management, with super-admin/operator access for system template authoring.

### Components

- `WorkflowCanvas`
  - graph rendering, pan/zoom, node selection, branch visualization
  - likely use a proven graph library such as React Flow rather than custom canvas logic

- `WorkflowPalette`
  - trigger/action/wait/condition catalog grouped by channel and control flow

- `WorkflowNode`
  - visual node variants for trigger, wait, branch, voice, SMS, email, PMS, notify, exit

- `StepConfigPanel`
  - right-side sheet/panel for per-step configuration
  - typed forms based on step type

- `WorkflowValidationPanel`
  - shows publish blockers and warnings
  - links errors to specific nodes
  - surfaces Part 12 compliance results: **content-class violations** (promotional language in an
    exempt-care/recall campaign), **PHI-in-body warnings**, missing consent path, and
    **blast-radius warnings** (large enrollment/projected spend requiring step-up approval).
    Backend validation remains authoritative; this panel renders its node-linked results.

- `WorkflowPublishControls`
  - save draft, validate, publish, pause, resume, duplicate, archive

- `WorkflowTemplatePicker`
  - starts from Appointment Confirmation, Appointment Reminder, Overdue Recall, Sales Qualification, and AI Callback templates

- `MessagePreview`
  - preview SMS/email text with sample merge data

- `TestRunDialog`
  - simulate workflow against sample contact/appointment/recall context without dispatching real sends

### API Client

- `workflow-api.ts`
  - list workflows
  - get workflow/detail/version
  - create draft from template
  - update draft definition
  - validate draft
  - publish version
  - pause/resume/archive
  - list templates
  - preview messages
  - run dry-run/test validation

### Backend APIs Needed By The UI

- Workflow draft CRUD.
- Workflow validation endpoint returning node-specific errors.
- Template clone endpoint.
- Publish/pause/resume endpoints.
- Version history read endpoints.
- Merge-field catalog endpoint by trigger/action/content class.
- Channel readiness endpoint from provisioning.
- Preview/test-run endpoint.

## End-To-End Implementation Approach

1. Add backend workflow API contracts before building the UI deeply.
2. Add frontend API client and TypeScript types matching backend schema.
3. Add campaign/workflow routes and sidebar entries.
4. Build campaign list and template clone flow first.
5. Build read-only canvas rendering of a workflow definition.
6. Add node selection and typed configuration panels.
7. Add add-node/add-edge interactions from a side palette.
8. Add validation panel linked to backend validation results.
9. Add draft save/publish/pause/version controls.
10. Add message preview and dry-run/test dialog.
11. Add responsive behavior: full canvas on desktop, list/step editor fallback on smaller screens if needed.
12. Add tests for API client, page rendering, role gates, and critical builder interactions.

## Architecture Decisions

- Use a graph library for the canvas. A workflow builder needs reliable pan/zoom, edges, selection, and node layout; custom implementation would add risk without product value.
- Backend validation is authoritative. Frontend validation improves feedback but must not be trusted for publish.
- Keep template configuration and free-form authoring on the same definition model. This avoids building two incompatible campaign systems.
- Use typed step config forms rather than raw JSON. Clinic admins should never edit raw workflow definitions.
- Store graph layout separately from execution semantics inside workflow definition metadata. Visual coordinates should not affect runtime behavior.

## Technical Considerations

- The builder must respect selected institution/location context. Location-scoped users should not edit another location's workflows.
- Long labels and message previews must not overflow compact panels or nodes.
- Published versions should be read-only snapshots.
- Draft autosave needs conflict handling if multiple admins edit the same workflow.
- Backend validation errors should include `node_id`, severity, message, and recommended fix.
- Some actions require channel readiness. The UI should surface missing Twilio/email/Retell setup before publish.
- The builder must prevent accidental activation after editing a draft. Publish should be an explicit action with confirmation.
- Accessibility should cover keyboard focus, panel navigation, labels, and non-canvas fallback for validation errors.

## Dependencies

- Workflow engine API and definition schema.
- Campaign templates for launch campaigns.
- Per-tenant messaging readiness APIs.
- Template/merge-field catalog.
- Role/permission decisions for operator vs clinic free-form authoring.
- Campaign management and progress UI.

## Edge Cases

- User opens builder for archived workflow.
- Workflow has active published version and editable draft at the same time.
- Validation fails because a channel is not provisioned.
- Node type exists in a published version but is no longer available in the palette.
- User changes location context while editing.
- Concurrent edit overwrites another user's draft.
- Browser refresh during unsaved canvas edits.
- Very large workflow graph becomes hard to navigate.
- Template clone fails halfway.
- Backend rejects publish because consent/content rules changed since draft was opened.

## Risks

- Canvas complexity can consume time before the runtime is stable.
- Free-form authoring may expose more power than compliance validation can safely support at launch.
- Frontend and backend schema drift can make saved definitions invalid.
- Poor UX around validation can cause admins to publish incomplete or non-compliant campaigns.
- Large workflow definitions can become difficult to diff, audit, and version visually.

## Validation Strategy

- Unit tests for workflow API client behavior.
- Component tests for campaign list, template picker, and config panels.
- Role-gate tests for campaign routes.
- Builder interaction tests for selecting nodes, editing config, and displaying validation errors.
- Snapshot or structural tests for rendering system templates.
- Accessibility checks for forms/panels/dialogs.
- Manual browser test for creating draft from template, editing SMS copy, validating, publishing, pausing, and viewing version history.

## Deployment Considerations

- Hide routes behind a feature flag until backend runtime and templates are ready.
- Start with read-only template viewer and guided configuration before enabling arbitrary graph edits.
- Add sidebar nav only for roles with access.
- Deploy validation/publish APIs before showing publish controls.
- Add analytics/logging for validation failures and publish attempts.
- Provide operator-only access in staging before clinic pilot rollout.

## Future Extensibility

- Free-form clinic authoring after template configuration stabilizes.
- Drag-and-drop from palette if initial click-to-add is simpler.
- Visual diff between workflow versions.
- Reusable subflows.
- AI-assisted copy suggestions with compliance validation.
- Collaborative editing and draft locks.
