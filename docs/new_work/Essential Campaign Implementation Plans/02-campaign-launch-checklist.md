# Essential 2 - Campaign Launch Checklist Implementation Plan

## What Needs To Be Built

Build a launch readiness checklist that runs before publish/activation and explains whether a campaign is safe to launch. The checklist should cover channel readiness, compliance, merge fields, audience size, exclusions, quiet hours, frequency caps, estimated sends, estimated cost, NexHealth data freshness, and staff handoff configuration.

This is a product surface, not just backend validation. A clinic admin should know exactly who will be contacted, what they will receive, what it will cost, and what still needs setup before the campaign can go live.

## Existing System Context

The backend already has:

- Workflow validation endpoint.
- Compliance metadata on workflow definitions.
- Channel readiness checks for SMS, email, and voice.
- Quiet-hours/compliance gate services.
- Emergency halt and pause/archive controls.
- Per-campaign usage attribution.
- NexHealth subscription/projection state for appointment triggers.

The frontend already has:

- `WorkflowValidationPanel`.
- `WorkflowPublishControls`.
- `ComplianceSettings`.
- Channel readiness warnings in the builder.
- Campaign detail lifecycle actions.

Current gap:

- Publish/launch still feels like generic validation.
- There is no single launch-readiness object that combines technical validity, compliance, data freshness, audience preview, and cost estimate.

## Existing Components To Reuse

- `WorkflowValidationService`.
- `channel_readiness` service.
- `ComplianceSettings` and validation panel UI.
- Existing usage/cost aggregation endpoints.
- Existing campaign run/enrollment APIs.
- NexHealth webhook subscription status and appointment working-set freshness.

## New Components Required

### Backend

- `CampaignLaunchChecklistService`
  - builds a checklist for a workflow draft or active version
  - returns blocking errors, warnings, and informational checks
  - calculates estimated audience and estimated send volume when an audience is available

- API endpoints:
  - `GET /automation/workflows/{workflow_id}/launch-checklist`
  - `POST /automation/workflows/{workflow_id}/launch-checklist/preview`

- Checklist sections:
  - workflow structure
  - merge-field readiness
  - channel provisioning
  - compliance classification
  - consent and suppression coverage
  - quiet hours and send windows
  - audience estimate and exclusions
  - NexHealth data freshness/capability
  - staff handoff/failure routing
  - estimated cost and send volume

### Frontend

- Launch checklist panel in builder.
- Launch confirmation dialog that shows blockers and warnings.
- Per-item links to the relevant fix surface:
  - channel setup
  - compliance settings
  - audience preview
  - message editor
  - NexHealth setup/status

## End-To-End Implementation Approach

1. Define checklist response schema with `status: pass | warning | blocked | unknown`.
2. Add backend service that composes existing workflow validation and channel readiness.
3. Add merge-field availability checks from the rich merge-field catalog.
4. Add compliance checks for content class, consent requirement, STOP/HELP copy, quiet hours, and frequency caps.
5. Add NexHealth checks for appointment/recall campaigns:
   - location has `nexhealth_subdomain` and `nexhealth_location_id`
   - appointment webhook subscription is active or recently reconciled
   - recall capability is supported when recall trigger is used
6. Add audience-estimate adapter for manual/bulk first, then segmentation plan outputs.
7. Add cost estimate using current channel attempt counts and configured pricing assumptions.
8. Update publish/activate flow to call checklist and show the launch dialog.
9. Allow publish to save a valid draft, but block activation when required launch checks fail.
10. Audit checklist acknowledgement when a user launches with warnings.

## Timeline

Estimated duration: 2 weeks.

- Days 1-2: checklist contract and backend composition of existing validation/readiness.
- Days 3-4: compliance, quiet-hours, frequency-cap, and merge-field checks.
- Days 5-6: NexHealth freshness/capability checks and audience/cost estimate stubs.
- Days 7-8: frontend checklist panel and launch confirmation dialog.
- Days 9-10: activation gating, audit logging, tests, and pilot checklist tuning.

## Architecture Decisions

- Separate publish validity from launch readiness. A workflow can be structurally publishable but not launch-ready for a location.
- Return all checklist items in one API call so the UI can show a complete picture instead of failing one issue at a time.
- Treat unknown audience size as a warning for manual campaigns, but a blocker for automated broad campaigns.
- Store launch acknowledgement metadata on activation, not on every checklist view.

## Technical Considerations

- Some checks are version-specific while others are location-specific. The response should show the workflow version and location context used.
- Cost estimates are estimates. They should be labeled as projected and based on planned attempts, not guaranteed spend.
- Quiet-hours and frequency-cap checks need to evaluate the campaign's first send time and likely retries.
- NexHealth sync status can lag outside business hours, so freshness thresholds should be configurable by campaign type.

## Dependencies

- Rich merge-field catalog.
- Audience preview and segmentation plan.
- Usage/cost reporting endpoint.
- NexHealth data-flow plan for webhook/reconciliation status.
- Compliance and consent service behavior.

## Edge Cases

- Campaign has multiple channels, but one channel is unprovisioned.
- Campaign has no current audience because it is event-triggered.
- Location has stale NexHealth projection but live revalidation would still work.
- User changes selected location after opening checklist.
- Campaign is paused and edited while checklist is open.
- Large campaign exceeds estimated send or cost threshold.

## Risks

- Checklist becomes noisy and users ignore it.
- Blocking too much prevents demos or safe internal testing.
- Cost estimates are perceived as exact billing promises.
- A broad campaign launches with unknown consent/audience state.

## Validation Strategy

- Unit tests for each checklist item and aggregate status.
- API tests for tenant/location scoping.
- Frontend tests for pass/warning/blocker rendering and publish gating.
- Manual staging test with:
  - fully ready appointment reminder
  - missing SMS provisioning
  - invalid merge field
  - stale NexHealth subscription
  - high-volume audience warning

## Deployment Considerations

- Ship checklist read-only first.
- Then make activation require no blockers for production locations.
- Keep an operator override for staging/internal only, audited.
- Add analytics on common blockers so onboarding gaps are visible.

## Future Extensibility

- Approval workflow for high-volume or marketing campaigns.
- DSO-level policy checks and locked compliance copy.
- Budget caps and send caps per campaign/location.
- Weekly readiness drift report for active campaigns.
