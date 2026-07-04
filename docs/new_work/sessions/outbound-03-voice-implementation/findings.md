# Phase 3 (Outbound Voice) — Validated Findings

**Date:** 2026-07-04 · **Branch:** `ali/phase-2` (`5083d02`) · **Alembic head:** `20260706_dnc_scope`
**Method:** scope §3.5/§7 + Plan-03 doc + report + `plan-03-findings.md`, cross-referenced against current code
(execution-flow trace, every claim `file:line`). **Planning only — nothing implemented.**

## Verdict on the existing report findings — all CONFIRMED against current code
Plan 03 is a functional **fire-and-forget v1 (~35%)**: places a per-location Retell call as a compliance-gated
workflow action; the "handle the outcome and react" half is unbuilt. Confirmations:

| ID | Finding | Status | Key evidence |
|---|---|---|---|
| V-1 | No outcome feedback loop | **CONFIRMED** | Executor `complete_step("call_placed")`→advance (`voice_node_executor.py:164-165`, fire-and-forget); webhook `RetellCallWebhook` has `extra="ignore"` + **no `metadata` field** (`webhooks.py:57,50-75`); `process_retell_call_analyzed_event` correlates only by `agent_id`→location (`:348-350`), never to a run. No dial-outcome branching. |
| — | **wait-for-event-or-timeout primitive missing** (scope §6.3 Critical) | **CONFIRMED (NEW emphasis)** | `WaitNode` supports only duration/calendar (`definition_schema.py:89-92,121-128`); `resume_after_timer` resumes only from a fired timer via the beat poller (`step_dispatcher.py:231-300`, `tasks/automation_workflow.py:163-246`). No external-event resume. |
| V-3 | Marketing consent-basis not hard-enforced | **CONFIRMED** | `ConsentRecord` has **no `basis` column** (`sms_consent.py:33-80`); gate allows on any latest non-revoked voice record (`compliance_gate_service.py:149-207`); `check(run, channel_type)` takes **no content-class** (`:40-46`). Marketing basis is only a publish-time warning. |
| V-4 | No dedicated data model | **CONFIRMED** | Executor **ignores the `call_id` in the create-phone-call response** (`voice_node_executor.py:147-153`) — so nothing stores `retell_call_id`/`dial_outcome`; reuses the generic step ledger. No `outbound_voice_profiles`; no `calls`→run linkage (`call.py`, `post_call_service.py:369-399`). NOTE: `AutomationWorkflowStepExecution.result_metadata` (JSON) already exists → storing `retell_call_id` needs **no migration**. |
| V-6 | Transient errors fail the whole run | **CONFIRMED** | `except Exception` → `fail_run`, **no re-raise**, no 4xx-vs-5xx/timeout classification (`voice_node_executor.py:155-162`). `SendVoiceNode.max_attempts` exists but is **unused** by the executor (`definition_schema.py:150`). |
| V-7 | No `OutboundVoiceService`/`RetellOutboundClient` | **CONFIRMED** | HTTP call + payload + error handling inline in the executor (`voice_node_executor.py:120-162`). |
| Disc. | Disclosure supplied but not proven spoken | **CONFIRMED** | Executor sets `compliance_disclosure` dynamic var (`:30-43,132-139`); whether the agent prompt speaks it is Retell-side and unverified. Spoken-opt-out→voice-suppression is **not wired** (webhook has no DNC-intent path). |
| XC-1b | Voice crash-window idempotency | **CONFIRMED** | Guard is `already_sent` (completed step), claimed AFTER the POST; the whole task commits at the end, so a crash between POST and commit can re-dial. No provider idempotency key sent (`:120-140`). |
| V-5 | Voice metering absent | **CONFIRMED — DEFERRED to Plan 11** | Executor emits no `UsageEvent`; per product owner, out of Phase-3 scope. |

## NEW findings surfaced by the trace
- **N-1 (cleanup):** the inline `VoiceNodeExecutor` fallback at `step_dispatcher.py:200-203` is **dead code** —
  `send_voice` is registered in the action registry (`action_registry.py:39`), so dispatch always takes the
  registry path (`:195-199`). Harmless, but remove during V-7.
- **N-2 (correlation design):** the `metadata.workflow_run_id` hedge is **non-functional end-to-end** (dropped
  at the webhook). The robust correlation key is **`retell_call_id`** (we control it if we capture+store it),
  which is already the UNIQUE key on `Call` (`call.py:151-153`) and carried on the webhook (`webhooks.py:59`).
- **N-3 (resume shape):** `resume_after_timer` today knows only *WaitNode* (advance past) and *held-send*
  (stay put → re-run gate → **re-send**). A voice "sent-then-parked" node is a **third shape**: on resume it
  must **advance past** (the call already happened) — a new case is required (`already_sent` prevents a re-dial
  but the pointer logic still needs the new branch) (`step_dispatcher.py:266-296`).
- **N-4 (race):** a webhook resume can race the safety-timeout timer. Mitigation exists: `cancel_timers_for_run`
  (`scheduler_service.py:104-122`) + the `run.status == WAITING` guard (`step_dispatcher.py:257`) makes the loser a no-op.
- **N-5 (context source):** branch-on-outcome needs the resume to write `call_outcome` into
  `run.trigger_metadata` — that is the exact source `_evaluate_rule` reads (`tasks/automation_workflow.py:230`,
  `step_dispatcher.py:323`). So **no `ConditionNode` schema change** is needed.

## Classification by priority × area
- **P0/P1 correctness & compliance:** V-1 (+ wait-for-event primitive), V-3, disclosure enforcement + spoken-opt-out→suppression, crash-safe idempotency (XC-1b voice).
- **P1 reliability:** V-6 (transient retry + wire `max_attempts`).
- **P2 architecture/data:** V-4 (minimal = store `retell_call_id`/`dial_outcome`; fuller = profiles + `calls` linkage), V-7 (service extraction), N-1 cleanup.
- **Deferred:** V-5 → Plan 11.

## Buildable now vs blocked (see `ambiguity-review.md`)
- **Buildable with no external answer:** V-4-minimal (capture+store `retell_call_id`), the park/resume
  *mechanism* (external-event resume + third resume shape), correlation-by-`retell_call_id`, the
  branch-on-outcome plumbing (write `call_outcome` to `trigger_metadata`), V-6, V-7, the `basis` *column* +
  gate *threading* for V-3, disclosure *dynamic-variable* supply (already done).
- **Blocked pending external evidence/decision:** the **dial-outcome mapping** (Retell `disconnection_reason`
  enum → normalized outcomes, incl. voicemail detection) → blocks V-1's outcome semantics; the **consent-basis
  matrix** (which basis each content class requires; is the closeout's auto-captured callback consent legally
  "express") → blocks V-3's hard-block semantics; **disclosure spoken-verification** method; **create-phone-call
  idempotency-key support** and **response `call_id` shape**. Details in `ambiguity-review.md`.
