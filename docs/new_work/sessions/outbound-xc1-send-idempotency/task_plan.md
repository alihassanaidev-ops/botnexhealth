# Task Plan — XC-1: Send-time idempotency (SMS / email / voice)

**Started:** 2026-07-04
**Branch:** `ali/phase-2`
**Class:** C (correctness-critical, multi-file)
**Register item:** XC-1 (see `../outbound-followups-and-gaps.md`) — the top pre-volume correctness gap.

## Goal
Guarantee **exactly-one dispatch per (run, step)** across retries, quiet-hours hold→resume, and worker
crashes, for all three channels (SMS, email, voice) — scope §5.4 "exactly-one-action semantics: the same
step for the same contact is never dispatched twice." Bias to **at-most-once** (never double-contact a
patient) when a prior attempt's outcome is unknown.

## Current state / risk (to confirm in research)
- Attempt ledger: `AutomationWorkflowStepExecution`, unique `(workflow_run_id, step_id, attempt_number)`
  (`uq_automation_step_execution_attempt`); created by `runtime_service.begin_step` (attempt_number default 1).
- SMS/email executors: NO dedup — `begin_step` then send. Retry/redelivery/hold-resume can re-send.
- Voice executor (P0-3): guards on a COMPLETED `call_placed` step BEFORE dialing, but the claim is written
  AFTER the Retell POST (`complete_step`) → crash between POST and complete_step is not covered.
- MUST NOT break the quiet-hours hold→resume flow (Finding B) which also uses `begin_step` on the send node.

## Phases
- [x] **P0 Research** — trace `advance()` send-node path + hold/resume + `begin_step`/`resume_run` to see how the
  send node's step execution is created on first pass vs hold vs resume, so the idempotency claim doesn't collide
  with the hold step. → `findings.md`.
- [x] **P1 Design** — pick the claim mechanism (leverage the unique index as a "claim before side effect";
  decide re-entry semantics: completed-success → skip+advance; in-flight/unknown → skip (at-most-once)).
- [x] **P2 Implement** — a shared claim helper (runtime or a small mixin) used by all 3 executors.
- [x] **P3 Tests** — unit: first send proceeds; duplicate/redelivery skips (no second send) + still advances;
  hold→resume still works and sends exactly once; crash-after-send does not re-send.
- [x] **P4 Verify** — full unit suite green + real-Postgres integration (add/adjust an idempotency case) + graph update.

## Status
**COMPLETE (2026-07-04).** All phases done. Also fixed a latent hold→resume unique-index collision
uncovered during research. 1329 unit + 7 integration green. Residual crash-window tracked as XC-1b in the
register. Nothing committed. See `progress.md`.
