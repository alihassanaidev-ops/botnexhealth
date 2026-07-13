# Task Plan: Phase 2 Implementation Verification (v3)

## Goal
Perform the CTO-requested **implementation verification** across the entire Outbound
Engagement Engine. Go plan-by-plan, verify what's actually implemented **against the
code** (not against the prior report), and classify every finding into 4 buckets:

1. **Fully implemented & complete**
2. **Implemented but incomplete**
3. **Implemented but unnecessary → should be removed**
4. **Missing but genuinely required**

This is a **verification exercise, not an implementation pass.** No code edits.
Adversarial stance per CTO: "do not assume that because something was implemented,
it's correct or necessary."

## Method
- **12 parallel read-only subagents**, one per plan (+ cross-cutting folded in).
- Each: graphify-orient → inspect claimed code at `file:line` → check wiring/reachability
  → scan the plan's tests → classify into the 4 buckets with evidence → challenge any
  report claim that doesn't match code.
- Prior docs used as a **prior to challenge**, not trusted:
  - `verification-phase2-v2/report.md` (status doc)
  - `outbound-followups-and-gaps.md` (register)
  - `verification-phase2-v2/plan-NN-findings.md` (historical evidence)
- Depth: **Standard** (file:line + wiring + test scan; trusts the 1400-test green baseline).
- Special emphasis on **Bucket 3** (unnecessary/remove) — the blind spot the v2 report never hunts —
  and on **challenging every "not-required" claim** to confirm it's genuinely optional (not a rationalized Bucket 4).

## Deferred (out of scope — decided 2026-07-12, pending CTO sign-off)
- Plan 05 E-3/E-4 (per-tenant domain, DNS, warm-up, HTML) → QA/ops
- Plan 09 D-5/D-6 (live NexHealth staging validation) → QA
Verify these plans' **built** code; treat the deferred remainders as known-open, not new findings.

## Phases
- **Phase 1 — Fan-out:** 12 per-plan verifier subagents. Status: pending.
- **Phase 2 — Synthesis:** merge into one consolidated verification report
  (`report.md`) — per-plan 4-bucket table + global remove-list (B3) + global
  genuinely-missing list (B4) + test/migration health. Status: pending.

## Output
- `verification-phase2-v3/report.md` — consolidated verification
- `verification-phase2-v3/findings.md` — raw per-plan agent returns
- `verification-phase2-v3/progress.md` — run log

## Current Status
**Complete** ✅ — both phases done. 12 per-plan verifiers + synthesis. Deliverable: `report.md`
(4-bucket dashboard + global remove-list + prioritized fix-list + report corrections + QA rec).
Headline: Bucket 3 nearly empty (only Plan 01 dead registry seam); 2 real P1 findings (Plan 05
webhook, Plan 11 attribution); 1 Bucket-4 (Plan 08 U-2b DNC UI). Proceed to QA.
