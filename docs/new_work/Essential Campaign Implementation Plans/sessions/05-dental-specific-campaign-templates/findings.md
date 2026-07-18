# Findings And Decisions

## Requirements

- Expand the checked-in campaign template library from four generic/SMS-heavy templates to a dental-specific library.
- Preserve executable workflow definitions as normal `WorkflowDefinition` JSON; do not add template-only runtime semantics.
- Add metadata for category, goals/outcomes, supported channels, readiness checks, required merge fields, compliance class, default audience/eligibility, frequency cap, handoff reason, analytics mappings, and sample preview context.
- Add guided setup before opening the builder.
- Recall and treatment-plan templates must be PMS capability-gated by metadata because NexHealth resource support varies by PMS.
- Voice templates must not hardcode clinic-specific Retell agent IDs.

## Research Findings

- Binding docs 09-12 require local consent/suppression/DNC as source of truth; NexHealth `unsubscribe_sms` is only an additional blocking hint.
- Appointment and callback templates are broadly safe; recall and treatment templates need PMS capability metadata.
- Treatment-plan patient-facing copy must avoid procedure/fee/clinical specifics by default.
- The current workflow schema has no opaque settings block. Template metadata stays on the template API contract; created workflows persist category/description/content classification through existing supported fields.
- The existing backend instantiate route now creates and publishes workflow versions correctly, so the frontend can use it instead of cloning through the generic create endpoint.
- Voice workflow nodes require a non-empty `retell_agent_id`; the callback template uses a placeholder and the instantiate endpoint replaces it from guided setup or rejects the clone.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Keep template metadata outside `WorkflowDefinition` | The workflow schema forbids unknown keys and runtime semantics should stay in normal nodes/triggers. |
| Use backend instantiate for frontend cloning | It can persist workflow category/description/content class and safely apply voice-agent setup. |
| Represent recall/treatment support as template metadata gates | PMS capability services/audience preview land later; metadata gives the UI/checklist/future gating a stable contract now. |
| Classify unscheduled treatment follow-up as `sales` with consent required | It is revenue/treatment recovery-adjacent and avoids procedure/fee specifics in patient copy. |
| Use a voice-agent placeholder plus instantiate-time replacement | Avoids hardcoded clinic Retell IDs while keeping the checked-in template structurally valid. |
