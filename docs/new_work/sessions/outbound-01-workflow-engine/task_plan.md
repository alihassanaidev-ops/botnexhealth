# Task Plan: Outbound 01 - Workflow Engine

## Goal
Build the non-sending workflow engine foundation: workflow definitions, immutable versions, runs, timers, runtime state transitions, scheduler polling, and the compliance-gate seam.

## Current Status
Mostly complete for Dev A's non-sending engine. Product-ready outbound sends still depend on Dev B's compliance and channel delivery work.

## Completed
- [x] Automation workflow schema and models
- [x] Immutable workflow versions
- [x] Workflow runs and step executions
- [x] Durable timer table and scheduler service
- [x] Runtime service state transitions
- [x] Step dispatcher
- [x] Celery timer polling and dispatch tasks
- [x] Compliance gate protocol/stub seam
- [x] API lifecycle routes for create/list/get/update/publish/pause/resume/archive
- [x] Enrollment and run-read routes
- [x] Focused unit tests around engine services and routes

## Remaining
- [ ] Replace send-node stubs with Dev B channel handlers
- [ ] Replace `NoOpComplianceGate` with Dev B `ComplianceGateService`
- [ ] Finalize emergency halt semantics with CTO/Dev B
- [ ] Decide whether workflow creation should be draft-first or immediate publish
- [ ] Decide whether update should re-publish immediately or require explicit publish

## Decisions Needing Approval
| Decision | Owner | Status |
|----------|-------|--------|
| 01 <-> 12 gate request/response contract | Dev A + Dev B + CTO | pending |
| Emergency halt vs pause behavior | CTO / tech lead | pending |
| Draft-first vs create-and-publish workflow UX | Product / CTO | needs confirmation |
| PATCH definition creates new active version immediately | Product / CTO | needs confirmation |

