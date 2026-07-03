# Task Plan: Outbound 03 — Builder Backend Follow-ups

> Resolve the dependency-free backend follow-ups that the frontend-only Builder UI
> session (`outbound-02-builder-ui`) deliberately deferred, and clearly document
> what remains blocked and why.
> **Mode:** Auto / auto-approved. Branch: `feature/outbound-engagement-engine`.
> Backend lane (`src/app/…` + `tests/`). Frontend untouched.

## Goal
Review all 7 documented Builder follow-ups (+ 2 trade-offs) from
`outbound-02-builder-ui/findings.md §5`, investigate each against the live
backend, **implement every item that is independent and unblocked**, and defer
the rest with a concrete, code-grounded reason.

## Origin — the 7 follow-ups + 2 trade-offs (from outbound-02 §5)
1. Fix broken `instantiate` endpoint (TPL-01/02)
2. Backend node-linked validate endpoint
3. Merge-field catalog endpoint
4. Per-channel readiness endpoint
5. Non-destructive server test-run endpoint
6. Version-list endpoint
7. Server draft-with-definition lifecycle
- (T1) Optimistic-lock / ETag on concurrent edit
- (T2) `send_voice` template seed

## Per-item verdict (see findings.md for the evidence behind each)

| # | Item | Verdict | Basis |
|---|------|---------|-------|
| 1 | Fix `instantiate` | **IMPLEMENT** | Self-contained bug (TypeError). Fix mirrors working `create_workflow`. |
| 2 | Validate endpoint | **IMPLEMENT** | Validation logic already exists (`WorkflowDefinition.model_validate`); just expose it pre-publish, node-linked. |
| 6 | Version-list | **IMPLEMENT** | Data model fully supports it; only the route was missing. |
| 3 | Merge-field catalog | **DEFER** | No renderer exists — send handlers are stubs (Plans 03/04/05 own the authoritative field set). |
| 4 | Channel readiness | **DEFER** | Voice needs per-clinic Retell agent provisioning; "ready" contract unspecified; frontend already degrades gracefully. |
| 5 | Server test-run | **DEFER** | Entangled with Plan-06 runtime AND depends on the (missing) send-handler renderer. |
| 7 | Draft-with-definition | **DEFER** | Structural gap → product decision + schema migration. Root cause of instantiate's awkward semantics. |
| T1 | Optimistic lock | **DEFER** | Needs concurrency-semantics decision + frontend coordination (client must send a version token; it does not yet). |
| T2 | `send_voice` seed | **DEFER** | Same blocker as #4 — needs a clinic Retell agent id. |

## Phases
- **Phase 0 — Investigate.** Read every relevant backend file; grade each item. **Status:** complete
- **Phase 1 — Implement #1 instantiate fix.** create_draft + publish_version; return `WorkflowResponse`; regression test. **Status:** complete
- **Phase 2 — Implement #2 validate endpoint.** `POST /automation/workflows/validate` → node-linked `ValidationIssueResponse[]`; 3 tests. **Status:** complete
- **Phase 3 — Implement #6 version-list.** `GET /automation/workflows/{id}/versions` → history + `is_current`; 2 tests. **Status:** complete
- **Phase 4 — Validate & test.** pytest (automation suites) + ruff on touched files. **Status:** complete
- **Phase 5 — Document deferrals + graph update.** This folder; `graphify update .`. **Status:** in progress

## Definition of done
- Every unblocked item implemented with tests; automation suites green.
- Each deferred item has a concrete, code-grounded reason recorded here + in findings.md.
- No new lint errors on touched files. Frontend untouched.
