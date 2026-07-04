# Task Plan — Outbound Safety & Compliance (P0 bundle + Plan 12 semantics)

**Started:** 2026-07-04
**Branch:** `ali/phase-2`
**Class:** D (architectural, multi-module, multi-plan)
**Owner:** Ali (author Ali2047)

## Context (why this work, traced to scope)

The platform is ScaleNexus — an inbound AI voice agent for dental clinics. The
**Outbound Engagement Engine** (`docs/new_work`) makes it proactive: a multi-tenant,
timezone-aware workflow engine + visual builder that sends voice/SMS/email, gated by
compliance **before every dispatch** (Scope §11, §12).

Plans 01 (engine), 02 (builder), 03 (voice) are done. The compliance **gate** exists
(emergency-halt + quiet-hours + SMS consent) but its **semantics are deferred**, and
three P0 safety defects remain open. This session closes the safety/compliance last mile.

Each item is traceable to the scope:
- **P0-1** webhook fail-closed ← §3.5 "Webhook delivery is signature-verified… idempotently"
- **P0-2** email-consent identity ← §11 "consent capture… per channel"
- **P0-3** voice idempotency ← §12 "Idempotent dispatch so retries/restarts never double-contact a patient"
- **Plan 12 semantics** ← §11 (TCPA/CASL, bilingual EN/FR STOP, minimum-necessary PHI,
  cross-channel/cross-campaign + per-location/DSO suppression), Plan 12 doc components 2/4/5(scope)/dnc.

## Explicit scope EXCLUSION (product-owner decision — 2026-07-04)

The user directed: **no caps or limits on clinics/locations, and no tenant-based caps.**
Therefore the following scope/register items are **deliberately dropped** (not oversights):
- **P0-4** frequency caps (≤1/day, ≤3/week) — Plan 12 component 3 / `FrequencyCapService` / `contact_frequency_ledger`.
- **P1-12 / P1-3** spend caps + blast-radius / projected-spend step-up gate — Plan 12 components 6 / `BlastRadiusService`.
- **P2-6** per-location outbound **concurrency cap** (numeric limit). *Retell per-workspace isolation* stays as a scale note, but no numeric cap.

Deviation from Plan 12 doc noted: the doc treats frequency cap as a "v1 launch control /
condition of the TCPA healthcare exemption." Product owner has accepted that risk and
excluded it. Recorded here and in `findings.md` for traceability.

## Phases — ALL COMPLETE

- [x] **P0 — Research** — grounded findings for all surfaces → `findings.md`. **complete**
- [x] **P1 — P0-1 webhook fail-closed** — prod guard in `config.py` + defense-in-depth 403. **complete**
- [x] **P2 — P0-2 email-consent identity** — `email_hash` on ConsentRecord, `hash_email`, gate split, migration. **complete**
- [x] **P3 — P0-3 voice idempotency** — attempt-ledger dedup guard in `VoiceNodeExecutor`. **complete**
- [x] **P4 — content-class + PHI validator** — `ContentComplianceValidator` wired into publish + `/validate`. **complete**
- [x] **P5 — AI-voice consent/disclosure** — `compliance_disclosure` dynamic var + validator rules. **complete**
- [x] **P6 — bilingual FR STOP** — FR keywords + Unicode tokenizer in `twilio_webhooks.py`. **complete**
- [x] **P7 — DNC tiers** — `DoNotContact.scope`, scope-aware enforcement across ALL channels, migration. **complete**
- [x] **P8 — Builder surfacing** — panel already renders issues generically; new codes surface automatically. **complete (no change)**
- [x] **P9 — Verification** — 1294 unit pass (12 pre-existing fails proven via baseline worktree) + 6/6 real-Postgres integration. **complete**

**All phases complete. Caps deliberately excluded (product-owner decision). Nothing committed yet.**

## Sequencing rationale

P0 bundle first (small, high-severity, unblocks safe sends). Then Plan 12 semantics in
consent-substrate order: validators (P4) → voice consent (P5) → FR STOP (P6) → DNC tiers
(P7) → builder surfacing (P8). Every column-add migration MUST be idempotent
(`IF NOT EXISTS`) — repo baseline builds schema from live metadata (`create_all`), the
gotcha that broke fresh deploys twice already (P2-11).

## Key anchors (to confirm in research)
- Webhook signature: `api/routes/nexhealth_webhooks.py:87-88`; config default `nexhealth_webhook_secret=""`.
- Email consent phone-hash bug: `services/automation/compliance_gate_service.py` `_check_explicit_consent`.
- Voice executor: `services/automation/voice_node_executor.py`.
- Validator seam: `services/automation/validation_service.py` (`ContentComplianceValidator` Protocol, `NoOpContentValidator`).
- Consent models: `models/sms_consent.py` (`ConsentChannel`, `ConsentRecord`, `SmsSuppression`, `DoNotContact`).
- STOP handling: `api/routes/twilio_webhooks.py`.

## Status
**COMPLETE** — all phases done and verified (2026-07-04). See `progress.md` for the full record.
