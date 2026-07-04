# Phase 3 (Outbound Voice) — Implementation Plan

**Status: PLAN ONLY.** After web research (2026-07-04), the Retell/technical ambiguities are RESOLVED
(A-1 dial-outcome enum, A-3 `call_id`, A-4 no idempotency-key → use committed-before-send, A-6 prompt is
API-verifiable) — so **P1–P5, P7, P8, P9 are buildable**. The **only remaining hard blocker is A-5's legal
sign-off** (does the healthcare exemption still hold with frequency caps dropped; is auto-captured callback
consent "express"?), which gates the **consent-basis MATRIX values in P6** (P6's column + gate threading are
buildable now). See `ambiguity-review.md` → "RESEARCH RESOLUTION".
Branch `ali/phase-2` (`5083d02`), Alembic head `20260706_dnc_scope`. Every phase below cites the affected
files from the verified trace (`findings.md`).

## Guiding principle
Deliver the plan's second half — *react to how the call went* — by adding an **external-event resume** to the
engine and layering voice-outcome semantics on top of the existing park/timer/resume + attempt-ledger +
gate machinery. Build the **unblocked scaffolding first**; gate the outcome-mapping and consent-basis-matrix
behind their resolved ambiguities.

## Dependency graph (what must precede what)
```
P1 capture+store retell_call_id ──┐
                                  ├──► P4 webhook↔run correlation + resume ──► P5 branch-on-outcome
P2 external-event resume mechanism┘         (needs A-1 outcome mapping)         (plumbing unblocked;
   (park voice node + 3rd resume shape)                                          real branches need A-1)
P3 V-6 transient retry ....................................... independent
P6 V-3 consent basis (column+threading unblocked; MATRIX needs A-5) ... independent of P1–P5
P7 disclosure spoken-enforcement + opt-out→suppression (needs A-6) ... independent
P8 V-7 service extraction (refactor) ......................... best alongside P1/P4
P9 crash-safe idempotency (needs A-4) ........................ layers on P1
Deferred: V-5 voice metering → Plan 11
```

## Phases (safe order)

### P1 — Capture & store `retell_call_id` (foundation; UNBLOCKED, no migration)
Parse the create-phone-call response `call_id` and store it on the attempt via
`complete_step(result_metadata={"retell_call_id": ...})` (JSON column already exists).
- **Files:** `voice_node_executor.py:147-164`.
- **Regression risk:** low (additive). **Migration:** none.
- **Note:** depends on A-3 (response `call_id` shape) — confirm against staging before relying on it.

### P2 — External-event resume mechanism (UNBLOCKED; the core architectural addition)
Add the ability to resume a WAITING run from an external event (not just a fired timer): (a) park a voice
node WAITING after a successful send with a **safety-timeout timer**; (b) a new resume entry point
(webhook-driven task) that loads the run by `retell_call_id` and calls a resume; (c) a **third resume shape**
in `resume_after_timer` — "sent-then-parked voice" advances *past* the node (does not re-dial; `already_sent`
is the backstop).
- **Files:** `step_dispatcher.py` (advance() park case + `resume_after_timer:266-296` new shape),
  `runtime_service.py` (a resume variant that advances past a sent node), `tasks/automation_workflow.py`
  (new webhook-resume task paralleling `_dispatch_timer_async:163-246`), `definition_schema.py`
  (`SendVoiceNode` gains a "wait-for-outcome" flag and/or per-outcome routing — final shape TBD in P5).
- **Regression risk:** MEDIUM — touches the shared wait/hold-resume path. Mitigate: additive branches only;
  extend the real-Postgres integration suite (`test_automation_engine_integration.py`) with a
  park→external-resume case; keep WaitNode/held-SMS resume behavior byte-identical.
- **Race:** cancel the safety timer on webhook resume (`scheduler_service.cancel_timers_for_run`) + rely on the
  `run.status == WAITING` guard so timer-vs-webhook is at-most-once.

### P3 — V-6 transient-error retry/dead-letter (UNBLOCKED; low-risk; can go early)
Classify Retell errors: transient (timeout/5xx/network) → re-raise so the Celery task retries with backoff,
then dead-letter after `max_attempts`; permanent (4xx/misconfig) → fail the run as today. Wire the existing
(unused) `SendVoiceNode.max_attempts`.
- **Files:** `voice_node_executor.py:150-162`; possibly `step_dispatcher.py`/`tasks` for retry scheduling.
- **Regression risk:** LOW–MEDIUM — changes run-failure semantics; test both branches. **Migration:** none.

### P4 — Webhook → run correlation + outcome recording (mechanism UNBLOCKED; **mapping BLOCKED on A-1**)
On `call_analyzed`, look up the attempt/run by `retell_call_id`, record the raw signals
(`disconnection_reason`, `call_status`), and trigger the P2 resume. **The mapping raw→normalized outcome
(answered/no_answer/busy/voicemail/failed) is BLOCKED on A-1** (Retell enum) — implement the correlation +
storage now; land the mapping table only once A-1 is confirmed.
- **Files:** `retell/webhooks.py:309-596` (correlation + resume enqueue; add `metadata` field only if we adopt
  metadata correlation — otherwise use `retell_call_id`), a new outcome-mapping helper, optionally
  `post_call_service.py:369-399` / `call.py` (add `workflow_run_id` linkage — V-4 fuller).
- **Regression risk:** LOW to existing webhook (additive branch); reuse `RetellWebhookEvent` dedup.

### P5 — Branch-on-outcome (plumbing UNBLOCKED; real branches need A-1)
The resume writes `call_outcome` into `run.trigger_metadata`; a `ConditionNode` with `field="call_outcome"`
branches (no schema change — confirmed). Enables retry-on-no-answer, **voicemail→SMS fallback** (the SMS send
re-checks its own consent via the gate), book→exit.
- **Files:** the P2 resume task / `runtime_service.py` (write `call_outcome`); templates (define the branch graph).
- **Regression risk:** LOW (uses existing condition evaluation).

### P6 — V-3 consent-basis hard-block (column+threading UNBLOCKED; **MATRIX BLOCKED on A-5**)
Add a `basis` column to `ConsentRecord`; thread `content_class` (already on `ComplianceMetadata`) into
`ComplianceGateService.check(...)` and down to `_resolve_consent`; the publish validator hard-blocks a
marketing-class voice campaign lacking an express-basis consent path. **Which basis each content class
requires — and whether the closeout's auto-captured callback consent counts as "express" — is a product/legal
decision (A-5)**: build the column + threading; encode the matrix only once A-5 is answered.
- **Files:** `sms_consent.py:33-80` (+ **migration**, idempotent, off `20260706_dnc_scope`),
  `compliance_gate_service.py:40-46,201-207`, `step_dispatcher.py:157` (pass content_class),
  `validation_service.py`/`content_compliance_validator.py` (publish hard-block).
- **Regression risk:** MEDIUM — `check()` signature change touches all gate callers (dispatcher + both task
  paths); keep SMS/email + non-marketing voice behavior unchanged (default basis). Migration must be backward-compatible.

### P7 — Disclosure enforcement + spoken-opt-out→suppression (**BLOCKED on A-6 for the enforcement half**)
Ship a canonical outbound agent-prompt template that opens with `{{compliance_disclosure}}`; add it to the
outbound-voice readiness/attestation. Wire the post-call DNC-intent tag → **VOICE suppression/DNC** via
`SmsComplianceService`. Spoken *verification* method is A-6.
- **Files:** `voice_node_executor.py:30-43` (already supplies the var), `retell/webhooks.py` (tag→suppression),
  provisioning/readiness (`channel_readiness.py`) if attestation is added, `validation_service.py` (warning→gate).
- **Regression risk:** LOW.

### P8 — V-7 service extraction (UNBLOCKED refactor; do alongside P1/P4)
Extract the create-phone-call IO into a mockable `RetellOutboundClient`; an `OutboundVoiceService`
orchestrates resolve→claim→call→record. Remove the dead inline fallback (N-1).
- **Files:** new module(s), `voice_node_executor.py`, `action_registry.py:39`.
- **Regression risk:** LOW–MEDIUM (behavior-preserving refactor); covered by existing voice unit tests.

### P9 — Crash-safe idempotency (voice half of XC-1b; **BLOCKED on A-4**)
Send a provider idempotency key to create-phone-call if supported (A-4), and/or claim the attempt in a
committed-before-send step so a crash between POST and commit can't re-dial.
- **Files:** `voice_node_executor.py`, possibly `runtime_service.py`. **Migration:** none likely.

### Deferred — V-5 voice usage metering → **Plan 11** (Retell minutes/dials in the post-call webhook).

## Architectural implications
- **New capability: external-event resume.** The engine currently only resumes on a fired timer; P2 adds
  webhook-driven resume — this *is* the scope's Critical "wait-for-event-or-timeout" primitive (§6.3) and
  generalizes beyond voice (e.g. inbound-SMS-reply resume later). Design it generically, not voice-only.
- **Gate becomes content-class-aware** (P6) — a Plan-12 touch; `check()` signature grows a `content_class` arg.
- **Vendor IO separated from orchestration** (P8) — `RetellOutboundClient` seam.
- **No new "system of record" for calls** — correlation via `retell_call_id`, keeping the PHI Call row and the
  attempt ledger as-is (aligns with scope §3.5 "no full call DB").

## Verification strategy (per phase)
Unit tests for each executor/gate change; **extend the real-Postgres integration suite** with: park→external
resume, branch-on-outcome, transient-retry, and consent-basis allow/block. Full `pytest tests/unit` +
`tests/integration/test_automation_engine_integration.py` green before each merge; single Alembic head.

## Recommended safe order
**P1 → P3 → P8 (scaffolding/refactor, all unblocked) → P2 → P4/P5 (once A-1 resolved) → P6 (once A-5 resolved)
→ P7 (once A-6 resolved) → P9 (once A-4 resolved).** Nothing that depends on a blocked ambiguity ships until
that ambiguity is closed.
