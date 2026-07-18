# Findings And Decisions

## Requirements

- Expose backend-supported callback automation and voice outcome controls in the campaign builder.
- Keep callback automation opt-in through `callback_requested` workflows.
- Use `call_outcome` in normal condition nodes for Retell outcome branching.
- Surface callback/voice readiness through the existing launch checklist.
- Keep staff handoff routed through the existing callback queue.
- Avoid new PHI-heavy projections or guessed Retell custom-analysis fields.

## Research Findings

- Backend schema already supports `CallbackRequestedTrigger` and `SendVoiceNode.wait_for_outcome`.
- Frontend types/catalog already include `callback_requested`; the missing builder gap was voice wait/outcome branch exposure.
- Retell `call_analyzed` handling already resumes parked workflow runs through `resume_voice_outcome` and records voice response events.
- The existing callback automation template existed, but its readiness metadata and outcome labels were thinner than plan 08 requires.
- Campaign analytics can expose callback answered/unreachable labels using existing `voice_answered` and `voice_failed` rollup columns without a schema change.
- Launch checklist already includes generic channel, compliance, quiet-hours, NexHealth, and handoff checks; callback-specific source/voice/wait/fallback items were missing.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Builder branch helper inserts a standard condition on `call_outcome` plus booked/staff handoff exits | Matches existing runtime condition semantics and keeps advanced editing available. |
| Failed voice outcomes create staff handoff records alongside unknown outcomes | Plan 08/12 default ambiguous or failed voice outcomes to staff fallback. |
| Callback analytics labels use existing rollup columns first | Avoids a migration for UI labels while keeping plan 08 visible in analytics. |
