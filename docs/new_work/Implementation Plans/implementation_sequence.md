# Implementation Sequence — Outbound Engagement Engine

> **Purpose.** This is the cross-plan delivery spine for the 12 implementation plans in this
> folder. Individual plans (01–12) each describe *what* to build and their own internal rollout;
> this file describes the *order* to build them in across the whole project — the one artifact the
> plans didn't own (Gap Analysis Finding 16 / Gap 23).
>
> **Guiding principle (Finding 16):** *engine → one channel (SMS) → one campaign (Reminder,
> lowest legal risk) → expand.* A strict file-by-file order hides two realities: several tracks
> run **in parallel**, and a few items must **start on day 1** because of multi-week external
> latency even though they finish later.

---

## Sequence at a glance

```
        ┌─────────────────────────────────────────────┐
Day 1   │ DECISIONS + LONG-LEAD (start now, block later)│
        │  • NexHealth multi-key + webhook-cap confirm  │
        │  • Legal classification of each campaign      │
        │  • Part 10: Twilio sub-acct + A2P/toll-free   │
        │    registration, email domain + warm-up start │
        └─────────────────────────────────────────────┘
                          │ (lead times run in background)
   Phase 1  ── 01 Engine + durable scheduler  ──┐
            ── 12 Consent schema migration ─────┘ (parallel; gate wires into 01)
                          │
   Phase 2  ── 10 Provisioning finish ──┐
            ── 04 SMS ─────────────────┤ (parallel subsystems)
            ── 09 Data layer (webhooks)─┘
                          │
   Phase 3  ── 06 (Reminder template only) + 11 metering + 08 (read-only progress)
                          │   ◄── first live campaign, proof point
   Phase 4  ── 03 Voice ──┐
            ── 05 Email ──┘ (email gated on warm-up completing)
                          │
   Phase 5  ── 06 (Confirmation, Recall) + 07 AI callback
                          │   (Recall gated on legal classification + written consent)
   Phase 6  ── 02 full builder + 08 full analytics/ops + 11 cost dashboards
```

---

## Phase 0 — Decisions & long-lead items (start day 1)

Nothing here blocks *starting*, but everything downstream waits on the multi-week vendor clocks —
start them first or they become the critical path.

| Item | Plan ref | Why day 1 |
|---|---|---|
| Confirm NexHealth **multiple production keys + independent budgets**, and any **webhook-subscription cap** per partner | 09 (Gap 7 / Finding 4) | Determines whether multi-key sharding is viable or the event-driven read model is the only lever. |
| **Legal classification** of each campaign (exempt-care vs. marketing) | 12 (Findings 1–3) | Gates Recall & Sales Qualification consent basis; can't build them live without it. |
| Kick off **Twilio sub-account + A2P 10DLC / toll-free** registration for the pilot clinic | 10 (Finding 11) | A2P campaign approval ~10–15 days; toll-free ~3–5 days; a hard onboarding gate. |
| Start **email sending domain + reputation warm-up** for the pilot clinic | 10 / 05 (Finding 6) | New-domain warm-up is ~2–4 weeks; bulk email can't go live until warmed. |

---

## Phase 1 — Foundation

| Order | Plan | Depends on | Notes |
|---|---|---|---|
| 1 | **01 · Workflow Engine + durable scheduler** | — | Nothing runs without the durable timer/state machine. The single largest net-new build. |
| 1 (parallel) | **12 · Compliance & Consent** | 01 (for gate hooks) | The multi-channel **consent schema migration is independent** and can start immediately; the `ComplianceGateService` wires into 01's enrollment/dispatch hooks and `QuietHoursService`. **Treat 12 as a Phase-1 peer of 01, not a finale** — its schema must land before any channel sends. |

---

## Phase 2 — First channel + trigger data

| Order | Plan | Depends on | Notes |
|---|---|---|---|
| 2 | **10 · Per-tenant Messaging Provisioning (finish)** | Phase 0 registrations | Credential resolver + readiness model must exist before real sends. |
| 3 | **04 · Outbound SMS** | 01, 12, 10 | **First channel** — lowest legal/cost/concurrency risk. Consumes 12's gate; does not migrate consent tables. |
| 3 (parallel) | **09 · Integration & Data Layer** | 01 (trigger model) | Appointment webhooks + recall pull + live revalidation. Separate subsystem — parallel to 04. |

---

## Phase 3 — First live campaign (proof point)

| Order | Plan | Depends on | Notes |
|---|---|---|---|
| 4 | **06 · Appointment Reminder template only** | 01, 09, 04, 12 | Lowest-legal-risk campaign (exempt, opt-out sufficient). First end-to-end proof. |
| 4 (parallel) | **11 · Usage metering ingestion** | 04 webhooks | Ship metering **with** the first channel to accumulate history; rollups/dashboards come later. |
| 4 (parallel) | **08 · Progress UI (read-only)** | 01 runs | Watch sequence progress; pilot can be operator-driven, full UI not yet required. |

---

## Phase 4 — Expand channels

| Order | Plan | Depends on | Notes |
|---|---|---|---|
| 5 | **03 · Outbound Voice** | 01, 12, 10 | After SMS is proven — higher concurrency/cost + AI-voice consent/disclosure complexity (Finding 1). |
| 5 (parallel) | **05 · Outbound Email** | 01, 12, 10, warm-up | **Cannot bulk-send until the Phase-0 domain warm-up completes.** Email unsubscribe is a non-deferrable legal minimum. |

---

## Phase 5 — Remaining campaigns + callback

| Order | Plan | Depends on | Notes |
|---|---|---|---|
| 6 | **06 · Appointment Confirmation, Overdue Recall** | 03/04/05, 09, 12 | **Recall gated on the Phase-0 legal classification + written-consent** capture. Sales Qualification stays **deferred** with lead intake (Finding 15) — manual/CSV only. |
| 7 | **07 · AI Callback Handling** | 01, 03, 12 | Owns the AI-callback workflow template; default every location to `manual` mode. |

---

## Phase 6 — Full UI, analytics, ops hardening

| Order | Plan | Depends on | Notes |
|---|---|---|---|
| 8 | **02 · Visual Workflow Builder (full)** | 01 schema | Read-only/guided config can precede this; full free-form canvas hardens last. |
| 8 (parallel) | **08 · Campaign mgmt + analytics + ops (full)** | 01, 06, 11 | Full analytics, CSV enrollment, dead-letter replay, **emergency-halt** operator control. |
| 8 (parallel) | **11 · Cost rollups + dashboards (full)** | 04/03/05 usage | Hierarchical location → institution → DSO reporting. |

---

## Three ordering traps (why file-number order is wrong)

1. **10 (provisioning) is numbered last but must start first.** Its vendor registrations are the
   real critical path. The *code* finishes in Phase 2; the *onboarding clock* starts day 1.
2. **12 (compliance) is numbered last but is foundational.** The consent-schema migration must
   land before any channel (04/05) sends; the frequency cap + content validator gate everything
   from Phase 3 on. It is a Phase-1 peer of 01.
3. **11 (usage/cost) is not a discrete late phase.** Metering ingestion ships with the first
   channel (04) in Phase 3; only the rollups/dashboards come in Phase 6.

---

## Single linear order (if parallelism must be collapsed)

> 10-start → **01** → **12** → 10-finish → **09** → **04** → **11** (metering) →
> **06** (Reminder) → **08** (read-only) → **03** → **05** → **06** (Confirmation/Recall) →
> **07** → **02** (full) → **11 / 08** (full).

---

## Decision gates that block phases (must be answered, not built)

| Gate | Blocks | Owner |
|---|---|---|
| NexHealth multi-key budgets + webhook-subscription cap | 09 scale strategy | Product + NexHealth (developers@nexhealth.com) |
| Per-campaign legal classification (exempt vs. marketing) | 06 Recall, Sales Qualification | Product + healthcare-TCPA counsel |
| Retell workspace = guaranteed BAA/PHI boundary? | 03/10 tenancy model | Product + Retell |
| Email unsubscribe accepted as the consent minimum | 05 launch | Product |

*See `Outbound_Engagement_Engine_Scope_Gap_Analysis.md` for the full basis of each gate.*
