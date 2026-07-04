# Task Plan — Phase 3 (Outbound Voice) Implementation Planning

**Started:** 2026-07-04
**Branch:** `ali/phase-2` (`5083d02`)
**Class:** D (architectural planning) — **PLANNING & VERIFICATION ONLY. No code changes.**
**Sources of truth:** `Outbound_Engagement_Engine_Scope.md` (§3.5, §7), `Implementation Plans/03-outbound-voice-calling.md`,
`verification-phase2-v2/report.md`, `verification-phase2-v2/plan-03-findings.md`.

## Goal
Produce a complete, validated, execution-ready implementation plan for Plan 03 with: verified findings,
phased plan (deps + safe order + affected files + regression risks + architectural implications), and an
explicit Ambiguity Review. Do NOT implement. Do NOT guess — document blockers instead.

## Method (planning-with-files)
- P0 Re-read scope §7 + Plan 03 doc + report + plan-03-findings (done this session; re-confirm).
- P1 Trace the complete execution flow against CURRENT code (dispatch → send node → wait/timer/resume;
  Retell create-phone-call; post-call webhook; attempt ledger; consent gate) — subagent + self-verify.
- P2 Cross-reference every Plan-03 finding (V-1..V-7 + disclosure + consent-basis + crash-idempotency) with
  code; mark CONFIRMED / CHANGED / NEW. Identify affected files + regression risks + arch implications.
- P3 Write deliverables: `findings.md`, `implementation-plan.md`, `ambiguity-review.md`.

## Findings to validate (from report + design discussion)
- V-1 outcome feedback loop (webhook↔run correlation, dial-outcome, branch-on-outcome, voicemail→SMS) + the
  missing **wait-for-event-or-timeout** primitive.
- V-3 marketing consent-basis hard-block (needs a `basis` field).
- V-4 dedicated data model (voice attempts w/ retell_call_id + dial_outcome; profiles; calls linkage).
- V-6 transient-error retry/dead-letter (classify errors, re-raise transient).
- V-7 service extraction (RetellOutboundClient / OutboundVoiceService).
- Disclosure spoken-enforcement + spoken-opt-out→suppression.
- Crash-safe idempotency (voice half of XC-1b).
- V-5 voice metering → **explicitly deferred to Plan 11** (per product owner).

## Status
**PLANNING COMPLETE; IMPLEMENTATION IN PROGRESS — 2026-07-04.** A-5 signed off (dev/research); A-1/A-3/A-4/A-6
resolved via research. Implementing full plan (P1–P9; V-5 deferred to Plan 11) in safe order. Progress in
`progress.md`. Auto-approve authorized by user.
