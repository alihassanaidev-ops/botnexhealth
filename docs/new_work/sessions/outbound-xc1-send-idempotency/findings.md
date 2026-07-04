# Findings — XC-1 send-time idempotency

## Flow (verified in `step_dispatcher.py`)
- `advance()` handles a send node (`SendSms/Voice/EmailNode`, L138): revalidate → gate.check →
  block(159)/hold(169)/allow → executor via `get_action_executor` (L195-199).
- Each of skip(144), block(159), hold(169), and the executors calls `runtime.begin_step(step_id=node.id)`
  with the DEFAULT `attempt_number=1`.
- **Hold path** (Finding B fix): begin_step(node.id) attempt 1 → status WAITING → timer → `wait_run`.
- **Resume** (`resume_after_timer`, L231): finds the WAITING step, `resume_run` sets it **COMPLETED**
  (result_code stays None), leaves `current_step_id` on the send node, calls `advance()` again.

## ⚠️ Latent bug discovered (pre-existing, from the Finding-B hold fix)
After a hold→resume, `advance()` re-enters the SAME send node and calls `begin_step(node.id)` again with
`attempt_number=1` — but the hold already created attempt 1 (now COMPLETED). `begin_step` does
`add + flush`, so this **violates `uq_automation_step_execution_attempt (workflow_run_id, step_id,
attempt_number)` → IntegrityError**. This affects EVERY post-resume continuation (executor send, and the
skip/block/hold-again branches). It is **untested**: unit tests mock the runtime (no real unique index);
the only integration resume test uses a `WaitNode` (distinct node id, no collision), not a held send.
→ XC-1 must fix this collision, and doing so via next-attempt allocation also delivers idempotency.

## Idempotency vectors
- **Task redelivery / re-advance** of an already-sent run → re-send (SMS/email have no guard).
- **Hold→resume** → collision (above) / potential re-send.
- **Voice (P0-3)** guards on a COMPLETED `call_placed` step, but its begin_step is attempt 1 too (collides
  after a hold), and the guard runs before begin_step.
- **Worker crash mid-send**: the whole Celery task runs in one transaction that commits at the end, so a
  crash rolls back `begin_step` too → on retry there is NO committed claim → re-send possible, and the
  vendor may already have sent. True crash-safety needs a **committed-before-send claim** (own transaction)
  or a **provider idempotency key** — deeper than this slice. See "Residual" below.

## Design (this slice — XC-1)
1. **Root-cause fix:** `begin_step` allocates the **next** `attempt_number` for `(run, step_id)` when not
   given one (`max(existing)+1`, else 1). Fixes the hold-resume collision and gives real per-attempt tracking.
   All current callers pass no attempt_number, so behavior is: first begin_step → 1, subsequent → 2, 3…
2. **Idempotency guard:** runtime `already_sent(run, step_id)` = a COMPLETED step with a success result_code
   (`sent` | `call_placed`). Each send executor checks it FIRST; if already sent → skip the vendor call and
   return `node.next_node_id`. Uniform across SMS/email/voice; replaces the voice P0-3 bespoke probe.
   - Hold's resumed step has result_code=None → NOT "already_sent" → send proceeds (correct).
3. Success result codes: SMS/email `"sent"`, voice `"call_placed"`.

## Residual (document as XC-1b, do NOT fake it)
The crash-between-send-and-commit window (double-send after a hard worker crash) is NOT closed by a
same-transaction guard. Real fix = a committed-before-send claim (separate session, like
`record_usage_event`) and/or a **provider idempotency key** (Resend `Idempotency-Key` header; Twilio /
Retell where supported) keyed `{run.id}:{node.id}`. Recommend as the follow-up; this slice closes the
common redelivery/hold-resume/re-advance vectors + the latent collision.

## Anchors
- `runtime_service.py` `begin_step` L48-79, `complete_step` L81-93, `resume_run` L120-134.
- `sms_node_executor.py`, `email_node_executor.py`, `voice_node_executor.py` (execute).
- Unit tests use AsyncMock runtime → will stub `already_sent`/`begin_step`; integration test (real PG) proves
  hold-resume-send + duplicate-dispatch.
