# Inbound SMS Routing (S-2) & Spoken Opt-Out → Voice Suppression (V-2) — Detailed Report

> **Scope of this document:** a grounded, cited analysis of the two scope-necessary inbound/compliance items in
> the Outbound Engagement Engine — **Plan 04 / S-2 (free-text inbound SMS routing)** and **Plan 03 / V-2 (spoken
> opt-out → voice suppression, currently blocked as A-8)**. It reconciles the **product vision** (scope + gap
> analysis), the **plan specs** (03/04/12), the **recorded session decisions**, and the **current code reality**
> on branch `ali/phase-2`. This is a report only — no implementation prompt.
>
> **Compiled:** 2026-07-05. **Branch:** `ali/phase-2`. **Method:** cross-source research (scope, plans, session
> notes, and code verification). Reverify `file:line` before acting — the tree moves.

---

## 0. Executive summary

- Both items are **compliance-critical, not "channel polish."** The product owner **dropped frequency caps**,
  but the **TCPA healthcare exemption is legally conditioned on those caps** (≤1/day, ≤3/week). With no cap,
  **honoring opt-out — typed (S-2) and spoken (V-2) — is the primary legal safeguard** for outbound outreach.
- **S-2 is buildable today and is greenfield** (no prior design). Its "structured template-defined response" path
  is **the same mechanism Plan 06 C-1 needs** (the Confirmation confirm branch) — build it once, satisfy both.
- **S-2 is deliberately NOT an NLU/auto-conversation feature in v1.** Product decision: free-text replies create a
  **staff notification**; only **explicit template-defined keywords** drive a workflow event.
- **V-2 splits cleanly into a buildable half and a blocked half.** The write/suppression + wiring is buildable
  now; the **detection of the opt-out signal from Retell is blocked (A-8)** pending one real opt-out `call_analyzed`
  payload (or the agent's post-call analysis schema). **Do not guess the compliance trigger field.**
- **Plan 12 owns all consent/suppression schema.** Plans 03/04 *consume* the pre-dispatch gate; they must not
  create/alter consent tables. (`inbound_sms_messages` is a message log owned by Plan 04 — that's fine.)
- **An open decision blocks V-2's write:** suppression **scope + mechanism** (per-location all-channel vs
  voice-only) — see §6.

---

## 1. Product vision — why these two items exist

The engine is **compliance-first**: the platform, not the workflow author, enforces safety before every send.

- **Scope §5.1 (Compliance-first):** *"Consent, suppression, do-not-contact, and quiet hours are enforced by the
  engine before any send — not the author's responsibility to remember."*
- **Scope §11 (Communication compliance):** *"immediate opt-out honoring; do-not-contact suppression enforced
  before every dispatch."*

Two inbound signals feed that single authoritative pre-dispatch gate:
- **Typed** — patient SMS replies (STOP keyword + free text) via the Twilio inbound webhook → **S-2**.
- **Spoken** — patient opts out mid-call, surfaced by Retell's post-call analysis pipeline → **V-2**.

**Target suppression semantics (Scope §11, "Cross-channel & cross-campaign suppression"):**
> *"Opt-out / do-not-contact is scoped **per location** — each location sends under its own number and brand, so a
> STOP applies to that sender — and suppresses **all** outbound channels for that location; a privileged staff
> action can additionally set an **institution- or DSO-wide** do-not-contact … Suppression and consent records
> carry both the **channel** and the **location** they apply to."*

**Cross-channel nuance (Scope §5.4 + Plan 03 Technical Considerations):** a met-goal or opt-out **stops the
remaining steps on other channels for that run**, but **positive consent does NOT cross channels** — a
voicemail→SMS fallback must re-check the target channel's own consent + line-type through the gate, *not* inherit
voice consent.

**The no-caps ⇄ compliance interaction (critical):**
- `outbound-safety-and-compliance/task_plan.md` records the product directive: *"no caps or limits on
  clinics/locations, and no tenant-based caps."* Frequency caps (≤1/day, ≤3/week), spend caps, blast-radius, and
  per-location concurrency caps were **dropped, not deferred**.
- `ambiguity-review.md §A-5` flags the consequence: the **TCPA healthcare exemption is conditioned on those very
  frequency caps.** *"Relying on the exemption for transactional_care/recall while running uncapped is a legal
  question counsel must resolve."* **Therefore opt-out honoring (S-2 + V-2) becomes the primary compliance
  backstop, not a secondary nicety.**

---

## 2. S-2 — Free-text inbound SMS routing (Plan 04, P1)

### 2.1 Intended design (Plan 04 spec)

**Table `inbound_sms_messages`** (Plan 04 → "New Components Required › Data Model"):
- `institution_id`, `location_id`, `contact_id`
- Twilio message SID
- from/to **hash and masked** phone
- **encrypted body** (PHI)
- classified **intent enum**: `stop`, `start`, `help`, `free_text`
- linked `workflow_run_id` **if correlation is possible** (best-effort)

**`InboundSmsRoutingService`** (Plan 04 → "New Components Required › Services"):
> - handles free-text inbound replies
> - correlates by **sender/recipient number and recent open workflow runs**
> - in v1, **creates staff notifications rather than autonomous NLU conversation**
> - emits a workflow event **only for explicit keywords/structured responses that templates define**

**The v1 boundary — explicit product decision (Gap §6, "Two-way conversation handling — ⏸️ Deferred"):**
> **Client review:** *"A dedicated per-clinic conversational SMS agent is out of scope for v1. Instead, surface an
> in-app notification/alert when a patient replies, so staff can continue manually."*
> **Findings:** *"Accepted — v1 surfaces inbound replies as a staff notification via the existing in-app + SSE
> channel; the autonomous conversational agent is a later phase. Existing STOP/opt-out keyword handling stays."*

**Architecture decisions (Plan 04):**
- *"For v1, free-text replies create staff notifications and optionally pause the run. Do not build a
  conversational SMS agent until explicitly scoped."*
- STOP/START/HELP **stays in the SMS layer** as the SMS-specific signal that **feeds Plan 12's suppression**.
- End-to-end step 6: *"Update inbound SMS webhook to persist free-text replies and notify staff."*
- Reuse the *"existing notification service to alert staff for free-text replies in v1."*

**Aspirational (catalog primitives that make structured replies branchable):**
- Scope §6.1 — "Inbound reply / keyword" trigger: *"React to a patient's SMS reply or spoken response to branch
  or enroll."*
- Scope §6.3 — "Wait-for-event-or-timeout": *"Wait for a patient response up to a deadline, then continue."*
- Scope §10.1 (Confirmation): *"capture confirm / reschedule-request / decline / no-response; on confirm, write
  the confirmation status back to the PMS; on reschedule-request, branch to rescheduling or hand off."*

### 2.2 Code reality — what exists to build on

| What exists | Location |
|---|---|
| Inbound handler `inbound_sms` (route `POST /twilio/webhooks/inbound-sms`) | `api/routes/twilio_webhooks.py:74-149` |
| Intent classify (STOP/START/HELP, incl. French CASL forms; Unicode tokenizer) | `twilio_webhooks.py:57-71` (keywords `:43-48`) |
| STOP → `compliance.suppress`; START → `release_suppression`; HELP → TwiML | `twilio_webhooks.py:104-139` |
| **Free-text drop point** (logs "Inbound SMS ignored", empty TwiML) — the intercept seam | `twilio_webhooks.py:141-149` |
| Location resolve by `To#` (`twilio_from_number`, `is_active`); dead-letters unmatched | `twilio_webhooks.py:240-256` |
| phone→contact lookup (`Contact.phone_hash` index; `find_by_phone_hash`) | `models/contact.py:73,152-155` |
| contact→active-run index (`ix_automation_workflow_runs_contact`); `status=WAITING` | `models/automation_workflow.py:238,269,281` (`WAITING` `:43`) |
| **Resume pattern to MIRROR** — `resume_voice_outcome` / `_resume_voice_outcome_async` | `tasks/automation_workflow.py:741-856` |

**The resume template (how `resume_voice_outcome` works — mirror this for an SMS reply):**
1. Find the parked step: `SELECT ...StepExecution WHERE status=WAITING AND result_code=_CALL_PLACED_AWAITING`,
   then match `result_metadata["retell_call_id"]` in Python (`:797-811`).
2. Load run, **guard `run.status == WAITING`** (at-most-once) (`:813-815`).
3. Cancel timers: `scheduler.cancel_timers_for_run(run.id)` (`:819`).
4. Write into context: `md = dict(run.trigger_metadata or {}); md["call_outcome"] = ...` (`:822-825`).
5. Resume: `dispatcher.resume_after_timer(run, definition, context=md, ...)` then commit (`:842-850`).
- Parking constant `_CALL_PLACED_AWAITING = "call_placed_awaiting_outcome"` in
  `services/automation/voice_node_executor.py:49`, written on the step at `:253`.

### 2.3 What's absent (net-new)
- **No inbound-message model/table.** Grep for `inbound_sms_messages` / `InboundSmsMessage` / `inbound_message`
  across `src/` → **zero matches**. Inbound SMS is processed transiently and never persisted.
- **No free-text reply → run resume path.** No SMS-analog "parked awaiting reply" step/constant, and no
  phone→contact→active-run lookup wired into `inbound_sms`. This is the net-new build, mirroring §2.2's voice
  resume template.
- **No prior design for S-2 in any session file** — `outbound-04-sms/` covers **outbound send only**
  (*"Voice/Email remain stubbed"*). S-2 is greenfield.

### 2.4 What S-2 actually is (synthesis)
Build `inbound_sms_messages` + `InboundSmsRoutingService` that, at the `twilio_webhooks.py:141-149` seam:
1. **Persists every inbound reply** — encrypted body, hashed/masked phones, intent-classified, correlated to a
   contact (phone_hash) + the most recent open run for that contact/location.
2. **For free text** → creates a **staff notification** (existing in-app + SSE), optionally pausing the run. No NLU.
3. **For a template-defined structured keyword** (e.g. a Confirmation `YES`/`C`) → **emits a workflow event /
   resumes the parked run early** (mirror the §2.2 voice resume; write `appointment_status=confirmed` into
   `trigger_metadata`, take the confirm branch).

**Coupling to Plan 06 C-1:** step 3 **is** the Confirmation confirm-branch fix (register C-1). Building S-2 with
the structured-keyword path retires Plan 04's inbound gap **and** Plan 06 C-1 in one coherent piece — but strictly
as a defined keyword, never natural-language interpretation.

### 2.5 Edge cases to honor (Plan 04 / Plan 12)
- Inbound reply from a **shared family phone** with multiple contacts → ambiguous correlation.
- STOP received while a run has future **cross-channel** steps (must suppress across channels for the location).
- START after a campaign suppressed a run.
- Bilingual / mixed-case / punctuated STOP (keyword robustness — already handled by the Unicode tokenizer).
- Reply arriving **after** the run is already terminal → must NOT re-open it (at-most-once; the `WAITING` guard).
- Delivery callbacks that arrive after a run ended → delivery-log update, not a state transition.

---

## 3. V-2 — Spoken opt-out → voice suppression (Plan 03, P1 — currently blocked as A-8)

### 3.1 Intended design (Plan 03 + Plan 12)
- **Plan 03 (Technical Considerations):** *"The outbound agent prompt must include in-call identity disclosure +
  an opt-out path … **Route the patient's spoken opt-out (Retell tag) into Part 12 suppression.**"* Edge case:
  *"Patient asks to opt out during a voice call."*
- **Gap §12 (findings):** *"for voice, use Retell conversation tags → set a DNC flag … treat the LLM tag as
  best-effort and add a manual staff DNC toggle."*
- **Plan 12:** the suppression `reason` enum includes **`voice_tag_dnc`**; `SuppressionService` writes it; the
  suppression is then honored by `ComplianceGateService` on every subsequent dispatch.
- **Disclosure enforcement** is a related requirement: for the opt-out to be *capturable*, the agent must actually
  **offer** the opt-out and disclose identity (Retell-config/product, not just backend code).

### 3.2 The A-8 blocker (verbatim — `outbound-03-voice-implementation/ambiguity-review.md §A-8`)
> **A-8 — Spoken-opt-out → suppression: how does Retell surface a DNC intent? — BLOCKING**
> - **Why blocking:** scope §7/§11 require routing a patient's spoken "stop" during an AI call into a VOICE
>   suppression/DNC. The post-call webhook has `call_analysis`/`custom_analysis_data`, but **how a "do-not-call"
>   intent is represented (a specific tag, a boolean field, a classification value) is not confirmed** in code or
>   docs. **Encoding a compliance suppression trigger by guessing the field is unacceptable.**
> - **Evidence needed:** a real Retell `call_analyzed` payload for a call where the patient opted out, or the
>   agent's configured post-call analysis schema, showing the exact field/value for DNC intent.
> - **Until resolved:** not implemented.

**Governing rule (`ambiguity-review.md` header):** *"where behavior can't be confirmed from code or docs, we do
not assume and do not implement that item."*

**Separability (`ambiguity-review.md §A-6`):** the wiring is buildable independent of the field detection —
*"we can still ship the canonical prompt template + the spoken-opt-out→suppression wiring; hold the automated
enforcement until [the field] is answered."*

### 3.3 Code reality — a clean split of buildable vs blocked

**Buildable now (two working levers to suppress voice):**
| Lever | Evidence |
|---|---|
| Write a **revoked VOICE `ConsentRecord`** → gate blocks voice | `record_consent(...)` is channel-aware `sms_compliance.py:333-362`; gate `_check_phone_consent(...VOICE...)` → `voice_consent_revoked` `compliance_gate_service.py:127-130` |
| Write a **`DoNotContact`** row (channel-agnostic, blocks all channels; `scope` location/institution/group) | `set_do_not_contact(...)` `sms_compliance.py:162-226`; model `models/sms_consent.py:159-216` |
| `ConsentChannel.VOICE` is a first-class value; a voice `SmsSuppression` row is DB-legal (per-channel unique idx) | `models/sms_consent.py:16-19,101-148` |
| Retell webhook already parses `custom_analysis_data` and **already reads a `send_sms` key from it** — an opt-out key reads the same way | `retell/webhooks.py:47,589` |
| Post-call insert point (guarded post-commit block); correlate by `retell_call_id` | `retell/webhooks.py:~410`, `process_retell_call_analyzed_event` `:313+` |
| `disconnection_reason` → outcome map | `services/automation/voice_outcome.py:16-50` |

**Blocked / missing:**
- **No opt-out/DNC-intent field is read anywhere** in the post-call path (grep of `retell/webhooks.py` for
  `opt`/`dnc`/`do_not_contact` → **zero matches**). `map_disconnection_reason` maps only dial/hangup enums — no
  opt-out concept. **This is the A-8 gap.**
- **`SmsComplianceService.suppress()` is SMS-hardcoded** (`sms_compliance.py:253`; `release_suppression`,
  `_active_suppression`, `assert_can_send` all filter `channel == SMS`). So even though a voice `SmsSuppression`
  row is DB-legal, **no voice suppression row can be written/read via the service today**, and the voice gate does
  not consult `SmsSuppression`. Suppressing voice today therefore means a **revoked VOICE `ConsentRecord`** or a
  **`DoNotContact`** row — *not* a voice `SmsSuppression` (that path would require extending `suppress()` + the gate).

### 3.4 What V-2 actually is (synthesis)
- **Blocked half (detection):** read a Retell opt-out/DNC signal from `custom_analysis_data` — requires the A-8
  evidence (a real opt-out `call_analyzed` payload **or** configuring the agent's post-call analysis schema to
  emit a known field). Also requires the agent prompt to **offer** the opt-out (disclosure enforcement).
- **Buildable half (write + wiring):** once the field is known, read it in `process_retell_call_analyzed_event`
  (the `:589` pattern), correlate by `retell_call_id`, and write the suppression via the chosen mechanism (§6).

### 3.5 Related recorded state
- `outbound-03-voice-implementation/findings.md`: *"Spoken-opt-out→voice-suppression is not wired (webhook has no
  DNC-intent path)"*; classified P0/P1 compliance work.
- `outbound-07-ai-callback/findings.md`: the merged gate **already requires a VOICE `ConsentRecord`** to place a
  call; the AI-callback path records an express VOICE consent on the inbound request (`source=system`,
  `reason="inbound_callback_request"`) — whether that legally qualifies as "express" is the open **A-5** question.
- **Disclosure enforcement (V-2 text half):** the `compliance_disclosure` dynamic variable is injected, but the
  *prompt-actually-speaks-it* verification is brittle (register/session notes) — a related follow-up.

---

## 4. Cross-cutting constraints (both items)
1. **Plan 12 owns all consent/suppression schema.** 03/04 consume `ComplianceGateService` / `SuppressionService`;
   they must not create/alter consent tables. `inbound_sms_messages` is a Plan-04-owned **message log**, not a
   consent table — allowed.
2. **No-caps ⇒ opt-out is the primary legal backstop** (§1). Elevates both items to launch-critical.
3. **The single pre-dispatch gate is authoritative:** `ComplianceGate.check(run, channel_type) → GateResult`
   (`allow|block|hold`), invoked for all send nodes at `step_dispatcher.py:~157`; `channel_type ∈
   send_sms|send_voice|send_email`. `hold` now **defers-and-resumes** (not terminate).
4. **Durable scheduler / wait-for-reply exists now.** The gap analysis predated the build; Plan 01 is 100%
   (timers, WaitNode, resume), so "wait for a reply up to N hours" is buildable on existing primitives.
5. **Frequency-cap piece of the gate is excluded by product decision** — do not implement or rely on it.

---

## 5. Suppression scoping — the open decision that blocks V-2's write
Scope §11 wants: **per-location default**, a STOP **suppresses all channels** for that location, with a
**privileged escalation to institution/DSO-wide**; records carry channel + location.

Code today diverges:
- `ConsentRecord`, `SmsSuppression`, `DoNotContact` scope by `institution_id` (NOT NULL); `location_id` is
  nullable and **not in unique indexes → default enforcement is per-institution**, not per-location.
- P7 added `DoNotContact.scope` (`location | institution | group`, default `institution`; migration
  `20260706_dnc_scope`) and made the gate enforce DNC on voice + email.
- `DoNotContact` is **channel-agnostic**; `SmsSuppression` is **channel-scoped** (but service is SMS-hardcoded).

**Gap §13 leaves these open:** *(a)* per-(location) vs per-(location, channel) granularity, *(b)* verify/migrate
whether today's `DoNotContact`/`SmsSuppression` is institution- or location-scoped, *(c)* reassigned-number hygiene.

**Implication for V-2:** the suppression **mechanism + scope** must be pinned before writing it —
- voice-only via **revoked VOICE `ConsentRecord`** (narrow, channel-scoped), **vs**
- all-channel per-location via **`DoNotContact(scope=location)`** (matches Scope §11's "STOP suppresses all
  channels for the location"), **vs**
- extend `suppress()` + gate for a voice `SmsSuppression` row.

---

## 6. Open decisions to resolve before implementation (route to product/legal)

### S-2 (inbound SMS routing)
1. **Structured reply keywords** that drive a workflow event (confirm set: `YES / Y / C / CONFIRM / 1`? bilingual?
   distinguish confirm vs reschedule vs cancel replies?). Everything else → staff notification (v1, no NLU).
2. **Correlation rule** when a reply is ambiguous — shared family phone / multiple active runs for one contact:
   which run receives the reply, or fall back to staff notification?
3. Confirm **v1 boundary**: staff-notification for all free text, workflow-event only for template-defined
   keywords (no conversational agent). (Product has already decided this — confirm it still holds.)

### V-2 (spoken opt-out)
4. **A-8 evidence (factual, blocking):** provide a real Retell opt-out `call_analyzed` payload, or the agent's
   configured post-call analysis schema, showing the exact DNC-intent field name + value. Do not guess.
5. **Suppression mechanism + scope** (§5): voice-only (revoked VOICE consent) vs all-channel per-location
   (`DoNotContact`) — a product/legal decision, given the no-caps → opt-out-is-primary-backstop posture.
6. **Disclosure enforcement:** confirm the agent prompt reliably offers the opt-out + identity disclosure (so the
   opt-out is capturable at all).

### Cross-cutting
7. **Suppression scoping default** (Gap §13): per-location vs per-institution; migrate/verify existing rows.

---

## 7. Recommendation
Treat these as **one coherent "inbound & opt-out" compliance workstream**, not two loose fixes:

1. **S-2 first (fully buildable, greenfield):** `inbound_sms_messages` + `InboundSmsRoutingService` + staff
   notification + the **structured-keyword resume path** — which **doubles as Plan 06 C-1** (Confirmation confirm
   branch). One build retires the Plan 04 inbound gap *and* Plan 06 C-1.
2. **V-2 second (split delivery):** build the **write + wiring behind a well-defined opt-out-signal seam** now;
   **gate the detection field on the A-8 evidence** (route to user). Keep the do-not-guess rule — a compliance
   suppression trigger must not be inferred.

Priority rationale: with frequency caps dropped, opt-out honoring is the primary compliance safeguard, so both
items are launch-critical — but S-2 is unblocked and delivers double value, while V-2 has a hard external
dependency (A-8) that should be surfaced immediately so the evidence can be gathered in parallel.

---

## 8. Source index
- **Product vision:** `docs/new_work/Outbound_Engagement_Engine_Scope.md` (§5.1, §5.4, §6.1, §6.3, §7.1, §7.2,
  §10.1, §11); `docs/new_work/Outbound_Engagement_Engine_Scope_Gap_Analysis.md` (§6, §12, §13, §14; Part II
  Findings 1, 3, 8, 13, 14).
- **Plan specs:** `docs/new_work/Implementation Plans/03-outbound-voice-calling.md`,
  `04-outbound-sms.md`, `12-compliance-and-consent.md`.
- **Session decisions:** `docs/new_work/sessions/outbound-03-voice-implementation/ambiguity-review.md` (§A-5, A-6,
  A-8), `.../findings.md`; `outbound-safety-and-compliance/{findings,task_plan,progress}.md`;
  `outbound-04-sms/findings.md`; `outbound-07-ai-callback/findings.md`; `outbound-12-compliance/findings.md`.
- **Status docs:** `docs/new_work/sessions/verification-phase2-v2/report.md` (§Plan 03, §Plan 04);
  `docs/new_work/sessions/outbound-followups-and-gaps.md` (V-2, S-2, S-3, C-1).
- **Code seams:** `api/routes/twilio_webhooks.py`, `retell/webhooks.py`, `services/sms_compliance.py`,
  `services/automation/compliance_gate_service.py`, `services/automation/voice_outcome.py`,
  `services/automation/voice_node_executor.py`, `tasks/automation_workflow.py`, `models/contact.py`,
  `models/sms_consent.py`, `models/automation_workflow.py`.
