# Findings And Decisions

## Requirements

- Provide launch checklist status items with `pass`, `warning`, `blocked`, or `unknown`.
- Combine workflow validation, merge fields, channel provisioning, compliance, consent/suppression coverage, quiet hours/send windows, audience estimate/exclusions, NexHealth readiness, handoff routing, estimated sends, and estimated cost.
- Expose `GET /automation/workflows/{workflow_id}/launch-checklist` and `POST /automation/workflows/{workflow_id}/launch-checklist/preview`.
- Surface checklist in the builder publish/launch flow.

## Research Findings

- Existing frontend surfaces from Graphify: `WorkflowPublishControls.tsx`, `WorkflowBuilder.tsx`, `workflow-api.ts`, `readiness.ts`, `WorkflowValidationPanel.tsx`.
- Existing backend surfaces from Graphify: `automation_workflows.py`, `WorkflowValidationService`, `ChannelReadinessService`, `ComplianceGateService`, NexHealth subscription/projection models.
- `prepare_outbound_sms_body` already appends clinic identity plus STOP/HELP copy at send time, so the checklist can report that as covered rather than requiring duplicate manual copy in templates.
- Audience preview and exact spend projection are not present yet; the checklist returns `unknown` for audience/cost and exposes per-contact planned attempts in metadata.
- Existing publish validation remains fail-closed for server validation errors. Channel readiness is advisory in the current system and remains advisory in Plan 02.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Keep checklist read-only for this slice unless blockers are already enforced by publish validation | Plan deployment says ship checklist read-only first, then make activation require no blockers. |
| Use a shared backend checklist service for saved and preview endpoints | Keeps the builder's unsaved draft preview aligned with the saved workflow checklist. |
| Show checklist in the right rail and publish dialog | The right rail gives continuous readiness feedback; the dialog gives launch-time visibility before activation. |
| Use `unknown` instead of estimates without audience data | Plan 07 owns audience preview/segmentation; exact volume/cost would be misleading before that dependency exists. |
| Block recall readiness until audience generation lands | Recall scan is the automated broad campaign path and needs the later audience adapter before safe launch. |

## Implemented Files

- `src/app/services/automation/launch_checklist_service.py`
- `src/app/api/routes/automation_workflows.py`
- `nexus-dashboard-web/src/types/workflow.ts`
- `nexus-dashboard-web/src/lib/workflow-api.ts`
- `nexus-dashboard-web/src/components/workflow/LaunchChecklistPanel.tsx`
- `nexus-dashboard-web/src/components/workflow/WorkflowPublishControls.tsx`
- `nexus-dashboard-web/src/pages/WorkflowBuilder.tsx`
- `tests/unit/test_campaign_launch_checklist_service.py`
- `tests/unit/test_automation_workflow_routes.py`
- `nexus-dashboard-web/src/test/workflow-api.test.ts`
- `nexus-dashboard-web/src/test/WorkflowBuilder.publish.test.tsx`
- `nexus-dashboard-web/src/test/WorkflowBuilder.render.test.tsx`
