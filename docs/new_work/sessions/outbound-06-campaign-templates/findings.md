# Findings: Outbound 06 - Campaign Templates

## Key Findings
- Templates are valid `WorkflowDefinition` dicts.
- Voice templates are excluded for now because voice needs clinic-specific Retell agent configuration.
- Template nodes can reference send types, but dispatcher send handlers are still stubs.
- Recall/reactivation templates are especially sensitive to legal/compliance classification.

## Open Questions
- Are recall and reactivation campaigns transactional care outreach or marketing?
- Who owns final patient-facing copy approval?
- Should tenants be allowed to edit template copy before activation?

