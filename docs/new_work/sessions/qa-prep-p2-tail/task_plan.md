# Task Plan: QA-prep — P2 tail (verification polish)

## Goal
Clear the P2/polish findings from the v3 verification that we deferred out of the
3-fix commit. None block QA; this closes the punch-list.

## Items
Backend (verifiable):
1. **Plan 01** — remove dead registry seam (`register_action_executor`,
   `supported_action_types`, `SUPPORTED_TRIGGER_TYPES`). Keep `get_action_executor`
   (used) and `_dispatch_send_stub` (real defensive fallback).
2. **Plan 10** — fix stale comment `validation_service.py:13-14` ("blocks publishing" →
   warning-only).
3. **Plan 11** — fix `recompute_usage_rollup.py` docstring (5-min → 15-min).
4. **Plan 07** — re-check `callback_resolved` at dispatch time (staff-resolve-during-ETA edge).
5. **Plan 12** — bilingual outbound STOP footer (inbound already bilingual).

Bigger / FE / judgment (assess, do clean parts, flag rest):
6. **Plan 02** — ETag/optimistic concurrency on live-campaign PATCH (BE 409 + FE If-Match).
7. **Plan 08** — per-campaign stat fallback (stop showing institution-wide under a
   campaign card) + `getUsageByCampaign` 200-cap. (FE — not build-verifiable.)
8. **Plan 12** — group-scope DNC *creation* (GROUP_ADMIN). Flag as feature, not polish.

## Constraints
- Re-run affected tests green.
- FE changes not build-verifiable locally (node_modules root-owned) — flag.
- No commit; provide message.

## Status
**Done for the safe/clear items; 4 flagged for a decision.** 1479 unit tests pass
(only the 3 pre-existing Redis-down appointment tests fail).

### Completed
1. ✅ Plan 01 — removed dead registry seam (`register_action_executor`,
   `supported_action_types`, `SUPPORTED_TRIGGER_TYPES`) + fixed stale module docstring.
   Kept `get_action_executor` (used) and `_dispatch_send_stub` (real defensive fallback).
   Grep-clean, 31 dispatcher tests pass.
2. ✅ Plan 10 — `validation_service.py` docstring: readiness is advisory (warns, doesn't block).
3. ✅ Plan 11 — `recompute_usage_rollup.py` docstring: 5-min → 15-min.
7. ✅ Plan 08 (FE, subagent) — `CampaignDetail.tsx`: secondary stat cards no longer fall back to
   institution-wide totals (show neutral 0); Bug-2 (200-cap) documented — backend `/by-campaign`
   has no per-campaign filter, so a future backend `workflow_id` filter is the real fix. FE not build-verified.

### Flagged — need a decision (not silent "polish")
4. Plan 07 — staff-resolve-during-ETA guard. Narrow edge (only with a future preferred time). Real
   design cost: would couple the generic enroll/dispatch path to callback semantics. → recommend defer.
5. Plan 12 — bilingual outbound STOP footer. Changes EVERY outbound SMS text (CASL product/compliance
   call). Inbound FR recognition already works. → CTO decision.
6. Plan 02 — ETag/optimistic concurrency on live-campaign PATCH. Coordinated BE+FE; low-frequency
   (single-admin-per-institution is the common case). → do BE+FE together later, or accept for pilot.
8. Plan 12 — group-scope DNC *creation* (GROUP_ADMIN). New feature + role scope, not polish
   (gate already honors GROUP scope). → separate feature ticket.
