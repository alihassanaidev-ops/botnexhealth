# Phase 3 (Outbound Voice) — Ambiguity Review

Per the planning rules: where behavior can't be confirmed from code or docs, we **do not assume and do not
implement** that item. Below is every uncertainty, why it blocks, and what's needed to resolve it.

## ⭐ RESEARCH RESOLUTION (2026-07-04, from public Retell docs + FCC/TCPA sources)
- **A-1 RESOLVED.** Retell `disconnection_reason` is a documented enum. Full set incl.: `dial_no_answer`,
  `dial_busy`, `dial_failed`, **`voicemail_reached`** (voicemail is reported via this reason — no separate
  flag), `ivr_reached`, `user_hangup`, `agent_hangup`, `call_transfer`, `call_take_over`, `invalid_destination`,
  `marked_as_spam`, `user_declined`, `telephony_provider_permission_denied`, `telephony_provider_unavailable`,
  `sip_routing_error`. **Mapping is now buildable** (see below). Caveat: "answered" isn't a disconnection reason
  — a connected call ends with `user_hangup`/`agent_hangup`; **"booked" comes from post-call analysis**
  (`custom_analysis_data`), not `disconnection_reason`.
- **A-2 RESOLVED (mitigated).** `metadata` is stored on the call object and "you can later get this field from
  the call object"; docs don't explicitly confirm it's in the `call_analyzed` webhook, but the webhook carries
  the call object. We use **`retell_call_id` correlation** regardless, so this is moot.
- **A-3 RESOLVED.** create-phone-call response (`V2PhoneCallResponse`) **includes `call_id`** — safe to capture+store (P1).
- **A-4 RESOLVED (negative).** create-phone-call has **no idempotency-key** header/param. → P9 must use a
  **committed-before-send claim** (own transaction), not a provider key. (Retell's own idempotency guidance is
  webhook-side: dedupe on `event + call_id` — we already do via `RetellWebhookEvent`.)
- **A-6 RESOLVED (feasible).** The agent prompt is retrievable: get-agent → `response_engine` (llm_id) →
  **get-retell-llm exposes `general_prompt`**. So we CAN programmatically verify the outbound agent prompt
  references `{{compliance_disclosure}}` in a readiness check (not attestation-only).
- **A-5 INFORMED — but a legal sign-off still required.** FCC Feb-2024 ruling: an AI-generated voice IS an
  "artificial voice" under TCPA. **Marketing** AI calls → **prior express WRITTEN consent**; **non-marketing**
  (informational/transactional) → **prior express consent**. **Healthcare exemption** covers appointment
  reminders/confirmations by a HIPAA covered entity — **but only if limited to 1 call/day, 3/week**. ⚠️ **This
  is the open decision:** the product owner **dropped frequency caps**, and the healthcare exemption is
  *conditioned* on those caps — so relying on the exemption for transactional_care/recall while running
  uncapped is a legal question counsel must resolve. CASL (Canada) governs separately. **Derived engineering
  baseline (pending sign-off):** marketing/sales → `express_written`; recall → at least `express`;
  transactional_care → `implied`/`exempt_treatment` *only if* the exemption still applies without caps.

**Net effect on the plan:** A-1/A-2/A-3/A-4/A-6 are resolved from public sources → the outcome-mapping (P4/P5),
correlation (P1), crash-safe approach (P9 = committed-before-send), and disclosure enforcement (P7) are now
**buildable**. The ONLY remaining hard blocker is **A-5's legal sign-off** — specifically the exemption-vs-no-caps
tension and whether the auto-captured callback consent qualifies as express — which gates the **consent-basis
matrix values** in P6 (the column + threading remain buildable now).

---
### Original ambiguity entries (superseded where marked RESOLVED above)

> **Implementation must NOT begin on the BLOCKED items (A-1, A-5, and confirmation of A-3) until resolved.**
> The unblocked scaffolding (plan phases P1, P2, P3, P8, and the column/threading parts of P6) may proceed.

## A-1 — Retell `disconnection_reason` / call-status enum values (incl. voicemail) — **BLOCKING (V-1 outcome mapping, P4/P5)**
- **Why blocking:** The whole point of Plan 03's second half is mapping the call result to a normalized outcome
  (answered / no_answer / busy / voicemail / failed / transferred) so the workflow can branch. In code the
  field is a free `str | None` (`retell/webhooks.py:72`) with **no enum and no mapping** anywhere in the repo.
  We cannot correctly build the mapping — or detect **voicemail** for the voicemail→SMS fallback — without the
  authoritative value set.
- **Evidence needed:** Retell's documented `disconnection_reason` / `call_status` value list (and how voicemail
  is signalled — a specific reason, an `in_voicemail` flag, or only post-call analysis), ideally plus a few
  real staging `call_analyzed` payloads for confirmation.
- **Until resolved:** build the correlation + storage (P1/P4) but do NOT ship the outcome-mapping table or
  outcome branches (P5).

## A-2 — Does Retell echo create-phone-call `metadata` back on the `call_analyzed` webhook? — **NON-blocking (mitigated)**
- **Why it came up:** we stamp `metadata.workflow_run_id` on the call, but the webhook model drops metadata
  (`extra="ignore"`, no field — `webhooks.py:57,50-75`), and it's unconfirmed whether Retell even returns it.
- **Mitigation (no external answer needed):** correlate on **`retell_call_id`**, which we control (capture from
  the create-phone-call response, P1) and which the webhook already carries + is the UNIQUE key on `Call`.
- **Action:** proceed with `retell_call_id` correlation; only pursue metadata correlation if A-2 is later
  confirmed and offers an advantage.

## A-3 — create-phone-call response body: is `call_id` present and in the expected shape? — **CONFIRM before relying (P1)**
- **Why:** P1/P4 correlation depends on capturing `call_id` from the response; today the body is never parsed
  (`voice_node_executor.py:147-153`). The developer's notes assume `{"call_id": ...}` but that isn't verified in code.
- **Evidence needed:** one real create-phone-call response from staging (or the API doc field name).
- **Until confirmed:** treat P1 as "confirm-then-implement"; a wrong field name silently breaks all correlation.

## A-4 — Does create-phone-call support an idempotency key? — **BLOCKING only for P9's approach choice**
- **Why:** P9 (crash-safe voice idempotency) prefers a provider idempotency key; if unsupported we must instead
  use a committed-before-send claim. Payload sends none today; no repo evidence either way.
- **Evidence needed:** Retell API support for an idempotency header/param on create-phone-call.
- **Until resolved:** don't build P9; the common double-dial vectors are already covered by `already_sent`.

## A-5 — Consent-basis matrix + legality of auto-captured callback consent — **BLOCKING (V-3 hard-block semantics, P6)**
- **Why blocking:** V-3 requires hard-blocking marketing-class (Recall/Sales) AI voice without an **express**
  consent basis. Two product/legal decisions are needed: (1) the **matrix** — which basis
  (`express_written` / `express` / `implied` / `exempt_treatment`) each content class requires for voice; and
  (2) whether the closeout's **auto-captured callback consent** (recorded from an inbound callback request,
  `source=system`, `reason="inbound_callback_request"`) legally qualifies as "express" for callbacks. We must
  not encode a compliance rule by guessing.
- **Evidence needed:** product + healthcare-TCPA/CASL-counsel decision on the matrix and the callback-consent basis.
- **Until resolved:** build the `basis` column + gate threading (structure), but do NOT encode the required-basis
  matrix or flip the validator warning to a hard error.

## A-6 — Disclosure spoken-verification method — **BLOCKING for P7's enforcement half (not the wiring)**
- **Why:** the platform supplies `compliance_disclosure`, but whether the AI **speaks** it depends on the Retell
  agent prompt (Retell-side). "Enforcement" needs a decision: can we verify the agent prompt contains the
  disclosure via the Retell API, or is it operator-attestation + a canonical prompt template only?
- **Evidence needed:** whether Retell's get-agent/LLM API exposes the prompt text for programmatic verification;
  product decision on attestation vs hard gate.
- **Until resolved:** we can still ship the canonical prompt template + the spoken-opt-out→suppression wiring;
  hold the *automated enforcement* until A-6 is answered.

## A-7 — Voicemail→SMS fallback preconditions — **derivative of A-1 + consent-capture**
- Depends on A-1 (detecting voicemail) and on the patient having **SMS** consent (the gate re-checks the SMS
  channel independently — correct by design). No new decision, but note the fallback only fires for
  SMS-consented contacts; the voicemail-detection half is blocked by A-1.

## Product/scope confirmations (low-risk, recommend confirming)
- **Run lifecycle change:** wait-for-outcome makes a voice send an **async wait** (run parks, resumes on
  webhook) instead of advancing immediately. Scope §7.2 clearly wants outcome feedback, so this is intended —
  but confirm there's no desired "fire-and-forget only" mode for any campaign.
- **Safety-timeout duration** for a never-arriving webhook (e.g. 30 min) — pick a default with product.

## Summary
- **Hard blockers before implementing the affected parts:** **A-1** (V-1 outcome mapping) and **A-5** (V-3
  basis matrix); **A-3** must be confirmed before P1 correlation is trusted.
- **Buildable now regardless:** P1 (pending A-3 confirm), P2 (external-event resume mechanism), P3 (V-6),
  P8 (V-7 refactor), and the column+threading structure of P6.
- **Recommendation:** proceed to implement the unblocked scaffolding; open A-1/A-3/A-4/A-5/A-6 with the
  relevant owners (Retell API docs/staging for A-1/A-3/A-4/A-6; product+legal for A-5) before building the
  outcome-mapping, consent-basis matrix, and disclosure-enforcement layers.
