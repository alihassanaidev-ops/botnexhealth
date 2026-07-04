# Plan 03 — Outbound Voice Calling — Verification Findings

Audited: 2026-07-04 (deferred from the 2026-07-03 v2 audit, which excluded Plan 03
because the other developer owned it and their branch was not accessible). Plan 03 has
since been **merged** (commit `0519e28`, Hammad) into `ali/phase-2` and integrated via the
action registry, and the Plan-12 compliance layer landed this session
(`../outbound-safety-and-compliance/`). Evidence is against actual code, not session docs.

## Scope of the plan vs. what was actually built

Plan 03 (`docs/new_work/Implementation Plans/03-outbound-voice-calling.md`) is broad:
dedicated outbound data model (`outbound_voice_profiles`, `workflow_voice_attempts`, `calls`
linkage), `OutboundVoiceService` + `OutboundVoiceConcurrencyService` + `RetellOutboundClient`,
a workflow action adapter, profile CRUD API + readiness UI, a **dial-outcome feedback loop**
(webhook correlates `retell_call_id` → attempt → workflow run; branch on
answered/busy/no-answer/voicemail/transferred), voicemail→SMS cross-channel fallback,
per-location concurrency, dead-letter replay, and voice usage metering — all under Plan-12
AI-voice consent/disclosure/opt-out.

The developer (`docs/new_work/sessions/outbound-03-voice/`) explicitly narrowed this to a
**"fire-and-forget v1"**: place the call as a workflow action and advance immediately; let the
existing post-call webhook record everything; defer the wait-for-outcome model. That is what
shipped.

## Verified — Completed

- **Outbound call as a workflow action** — `src/app/services/automation/voice_node_executor.py`.
  `VoiceNodeExecutor.execute` resolves contact → `contact.phone`, location →
  `retell_from_number`, `settings.retell_api_secret`, then POSTs Retell
  `v2/create-phone-call` with `override_agent_id = node.retell_agent_id`, dynamic vars
  (`first_name`, `user_number`, `clinic_name`, `compliance_disclosure`) and `metadata`
  (`workflow_run_id`, `institution_id`, `source`, `ai_automated_call`). Fail-safe on every path
  (`no_contact`/`contact_not_found`/`no_phone`/`no_from_number`/`retell_not_configured`/`send_failed`).
- **Registered in the action registry** — `action_registry.py`: `"send_voice" → VoiceNodeExecutor`.
  Plugs into the same dispatcher seam as SMS/email; no dispatcher edit needed.
- **Per-location caller identity** — migration `20260703_retell_from_number` adds
  `institution_locations.retell_from_number` (E.164). Distinct from `twilio_from_number` (SMS).
- **Reuses the inbound Retell chain** — the agent's function-call tools (lookup/slots/book/etc.)
  fire identically on outbound calls; no change to `retell/handlers.py`. Booking stays live against
  NexHealth. Inbound handlers regress-safe (untouched).
- **Does NOT write the `Call` row** — correctly leaves recording to the existing post-call webhook,
  which already branches on `direction=="outbound"` (`retell/webhooks.py`), avoiding a UNIQUE
  `retell_call_id` collision and duplicate retention logic.
- **Compliance-gated before dispatch** — the dispatcher runs `ComplianceGateService.check(run,
  "send_voice")` before the executor: emergency halt (block), quiet-hours (hold-and-resume),
  voice consent via `ConsentRecord` (phone-keyed), and — **added this session** — do-not-contact
  enforcement for voice (previously the gate checked DNC only for SMS; voice/email were a hole,
  now closed via `SmsComplianceService.is_do_not_contact`).
- **AI-call disclosure injected (this session)** — the executor passes a `compliance_disclosure`
  dynamic variable (clinic identity + automated-call disclosure + spoken opt-out) and an
  `ai_automated_call` metadata flag; the content validator emits `ai_voice_disclosure_required`
  and `ai_voice_marketing_needs_express_consent`.
- **Idempotency guard (this session, P0-3)** — before dialing, the executor checks the attempt
  ledger (`AutomationWorkflowStepExecution` completed with `result_code="call_placed"` for this
  `(run, node)`) and skips a re-dial on timer redelivery / task retry.
- **Tests** — `tests/unit/test_outbound_voice_executor.py` (contact/phone/from-number/Retell-config
  failure paths, success payload incl. disclosure, HTTP error, idempotent-skip). Pass.

## Missing (vs plan)

- **`outbound_voice_profiles` table** — NOT built. No `retell_workspace_id`, `outbound_retell_agent_id`
  (the node carries `retell_agent_id` instead), `max_concurrent_calls`, `is_active`, or per-profile
  encrypted credential reference. Outbound binding is a single `retell_from_number` column + the node's
  agent id; there is no first-class outbound profile.
- **`workflow_voice_attempts` table** — NOT built. There is no dedicated voice-attempt row with
  `retell_call_id`, encrypted/hashed/masked `to_number`, the `queued/initiating/in_progress/completed/…`
  status machine, or a `dial_outcome`. The generic `AutomationWorkflowStepExecution` ledger is reused.
- **`calls` linkage columns** — NOT added. No `workflow_run_id` / `workflow_step_id` /
  `voice_attempt_id` / `dial_outcome` on the `Call` model (dev findings confirm this).
- **Dial-outcome feedback loop — the central gap.** `metadata.workflow_run_id` is *written* but
  the Retell webhook (`retell/webhooks.py`) **never reads `metadata` or `workflow_run_id`** — there is
  zero correlation of a call's outcome back to its workflow run. The run advances the instant the call
  is *placed*, regardless of answered/busy/no-answer/voicemail. Consequently **branch-on-call-outcome,
  retry-unreachable, exit-on-booked, and voicemail→SMS fallback do not work.** `ConditionNode` cannot
  route on outcome because no outcome is recorded on the run.
- **`OutboundVoiceService` / `RetellOutboundClient` as isolated components** — NOT built; the HTTP call
  is inline `httpx` in the executor (mockable in tests, but not the separate, retry-policy client the
  plan specified).
- **Profile CRUD API + outbound readiness UI + campaign drill-down of attempts/outcomes** — NOT built.
- **Spoken opt-out (Retell tag) → Plan-12 voice suppression** — NOT wired.
- **Cross-channel fallback (voicemail → SMS) with per-channel consent re-check** — NOT built.
- **Voice usage metering (Retell minutes/dials)** — NOT captured (Plan 11 voice-metering TODO open;
  best wired in the post-call webhook, which has duration).
- **Voicemail kept `in_progress` until webhook completion** — NOT applicable (fire-and-forget advances).

## Bugs / implementation gaps

1. **No outcome correlation (by design v1, but a functional plan gap).** Webhook ignores
   `metadata.workflow_run_id`; the call outcome never reaches the run. Everything downstream of "the
   call was placed" (retry, voicemail fallback, book→exit) is absent.
2. **Disclosure depends on a manual Retell-dashboard change.** The executor now passes
   `compliance_disclosure`, but the surveyed live agent prompt (`ScaleNexusAI Main Agent … .json`) only
   references `first_name`/`user_number` — it does **not** speak `{{compliance_disclosure}}`. So the
   in-call AI-identity disclosure + opt-out (a TCPA/CASL requirement) is **not delivered until the
   Retell agent prompt is updated per location**. Code is correct; the compliance outcome is gated on
   an onboarding step that must be tracked.
3. **Transient Retell errors fail the whole run.** The executor catches the exception and `fail_run`s
   without re-raising, so task-level retry + dead-letter (A14) never fire — a transient 5xx permanently
   kills the run. The plan explicitly requires distinguishing vendor failure from patient outcome and a
   dead-letter replay path for failed initiation.
4. **Idempotency is claimed *after* the side effect, not before.** The pre-dial guard only matches a
   *completed* `call_placed` step, so it correctly prevents the common double-dial (task redelivery of
   an already-completed step). But a crash *between* a successful Retell POST and `complete_step` leaves
   a `pending` step; re-dispatch then calls `begin_step` again with `attempt_number=1`, colliding on
   `uq_automation_step_execution_attempt` (error, not a clean resume). The plan wanted the attempt row
   claimed as `initiating` before the call.
5. **Consent *basis level* not enforced for marketing-class voice.** `ConsentRecord` is granted/revoked
   only — there is no `express_written` vs `implied` basis. So "Recall/Sales require a written/express
   consent basis, checked before the call" is enforced only as *consent present*; the validator warns
   (`ai_voice_marketing_needs_express_consent`) but nothing hard-blocks a Recall/Sales voice send that
   has mere implied consent.

## Deliberately excluded (product-owner decision — not defects)

- **Per-location outbound concurrency** (`OutboundVoiceConcurrencyService`, `max_concurrent_calls`) and
  any budget/concurrency caps — **dropped**: the product owner directed NO caps/limits on
  clinics/locations (see `../no-caps` memory / `../outbound-followups-and-gaps.md`). Per-clinic Retell
  **workspace isolation** (BYO-SIP) remains a valid scale item (isolation, not a numeric cap), still unbuilt.

## RLS / tenancy

- No `workflow_voice_attempts` table exists, so its RLS requirement is N/A. The reused
  `AutomationWorkflowStepExecution` is `institution_id`-scoped with RLS (verified by the engine
  integration suite). No cross-tenant exposure introduced.

## Scope alignment verdict

Plan 03 is a **correct, well-integrated, thin v1 slice (~35–40% of the full plan)**. The core it
claims — initiate a Retell outbound call as a per-location, tenant-scoped, compliance-gated workflow
action that reuses the inbound function/booking chain — works end-to-end and is now hardened with DNC
enforcement, AI-call disclosure injection, and an idempotency guard from this session. But the plan's
larger half is unbuilt: the dedicated data model (profiles/attempts/calls linkage), and above all the
**outcome feedback loop** (webhook↔run correlation, dial-outcome, branch-on-outcome, voicemail→SMS)
that gives outbound voice its campaign value. Concurrency is intentionally out per the no-caps decision.
The two compliance caveats to flag before any real Recall/Sales voice campaign: the agent prompt must be
updated to actually speak the injected disclosure (bug #2), and marketing-class consent-basis level is
not hard-enforced (bug #5).
