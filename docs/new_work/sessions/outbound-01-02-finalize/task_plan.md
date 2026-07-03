# Task Plan — Finalize Plans 01 & 02 to 100% (production-ready)

**Date:** 2026-07-04
**Branch:** ali/phase-2
**Approved plan:** `C:\Users\AliHa\.claude\plans\ethereal-stargazing-metcalfe.md`
**Audit basis:** `docs/new_work/sessions/verification-phase2-v2/report.md`

## Scope decisions (locked)
- Compliance: build 01/02 validator seams + structural consent-path guardrail; Plan-12 semantic
  validators (promotional/PHI/blast-radius/frequency) remain seams.
- Lifecycle: keep create-and-publish (no draft-first).
- Canvas: full drag-and-drop.
- Cross-plan: build recall (09), readiness (10), metering (11), AND voice (03) fully.
  Voice reconciled against other dev's Plan 03 branch before merge.

## Phases — ALL COMPLETE (Status: complete) except flagged deferrals
- [x] P0 — session + integration test harness — **complete**
- [x] P1 — Plan 01 correctness-critical (A1,A2/A3,A4,A5/A6,A12) — **complete**
- [x] P2 — Plan 01 architecture (A7,A8,A9,A10,A11,A13,A14,A15,A16) — **complete**
- [x] P3 — Plan 02 integration (B1,B2,B3,B4,B8) — **complete**
- [x] P4 — Plan 02 (B5,B6,B7,B9) — **complete**
- [x] P5 — cross-plan (C09,C10,C11) — **complete**; C03 voice DEFERRED (other dev's branch inaccessible; seam ready)
- [x] P6 — verification (275 backend + 130 FE + 6 real-Postgres integration tests) — **complete**; found+fixed fresh-deploy migration bug

**Plans 01 & 02 = 100%, verified against real Postgres. All phases complete.**
Deferred (flagged to user): Plan 03 voice (branch inaccessible), Plan 09 full projection read model.

## Key anchors (verified)
- Inline enroll bug: `api/routes/automation_workflows.py:461-470` (no gate, UTC).
- Quiet-hours hold drop: `services/automation/step_dispatcher.py:124-130`.
- recover_stale_claims unwired: `worker.py:56-65` (only poll + recall beats).
- Idempotency: `services/automation/enrollment_service.py:52-75`; DB index `20260702_auto_workflow_core.py:266-268`.
- Emergency halt model/routes: `models/outbound_halt.py`, `automation_workflows.py:632/663/717`.
- Integration harness template: `tests/integration/test_rls_postgres.py:38-174`.
</content>
