# Outstanding Scope — Workstream Breakdown

A cross-cut of [OUTSTANDING_SCOPE.md](OUTSTANDING_SCOPE.md), organised by **which system and
skill-set owns the work** rather than by the source document's Part numbering. Every one of the 47
items appears exactly once under a primary workstream, with cross-references where it spans more
than one.

Sizes are **build days at agent-driven pace** — writing, reviewing and testing, with parallel
agents — and are for sequencing only, not commitments. The build column is no longer the expensive
part of this backlog; the section *What doesn't compress* is where the schedule actually lives.

---

## Headline numbers

| | |
|---|---|
| Total items | **47** |
| Build effort | **≈ 63 days** at agent-driven pace — roughly 3 weeks across parallel lanes |
| Fully inside this repo (Platform + dashboard) | **28 items** |
| Fully outside this repo (Cloud Service / Connector) | **7 items** |
| Split across both | **12 items** |
| Blocked on a product decision | **9 items** (Decisions A–I) |
| Gated on something other than code | **17 items** — see *What doesn't compress* |

---

## Delivered so far

| Item | Workstream | Commit | Notes |
|---|---|---|---|
| **32** · Record who changed a campaign | Security, audit & RBAC | `7572ca4` | 15 `CAMPAIGN_*` action types, 18 endpoints decorated, static coverage test. Four of five acceptance criteria met in full; compliance-setting audit is reserved for Item 20 (no such endpoint exists yet) |
| **14** · Retry text messages | Campaign engine core | `7116457` | Found worse than documented: `SmsService` never raises, and the executor discarded its return value, so a Twilio rejection was recorded as a delivered contact. Three-way classification — the ambiguous network case is deliberately not retried, since Twilio's Create Message has no idempotency key |
| **15** · Delivery results into campaigns | Campaign engine core | `cc3f28a` | Step records the provider message id in `result_metadata`; terminal receipts mark `sent:delivered` / `sent:undelivered`. Branching on delivery failure deferred — the run has usually advanced past the step by then |
| **4** · Report pending honestly — *Platform half* | GoTracker booking safety | `0d096f2` | `BookingWriteStatus` + `write_status` on `BookingResult`; GoTracker defaults to pending instead of scheduled; message follows status. Cloud Service half and run-history visibility (Item 11) still outstanding |

---

## Three codebases, not one

This is the first thing to get straight, because roughly a fifth of the backlog cannot be written on
this branch at all.

| System | Where the code lives | Items |
|---|---|---|
| **The Platform** — campaign engine, NexHealth integration, dashboard API | `nex_health/src/app` (this repo) | 28 fully, 12 partly |
| **The Dashboard** — clinic-facing React app | `nex_health/nexus-dashboard-web` (this repo) | ~14 items have a UI slice |
| **Cloud Service + Connector** — GoTracker path | **Separate codebase.** Only specs and CDK mirror locally at `../gotracker_synchronizer` (see `GoTrackerSync_Salman_Completion_Plan.md`) | 7 fully, 12 partly |

---

# WS1 · GoTracker booking safety
**5 items · ≈ 5 days · Cloud Service + Connector + Platform · TIER 0**

The highest-priority group in the whole backlog, and the only one where the software can currently
cause real-world harm with no error to warn anyone: double-booked slots in a live practice, and
patients told "confirmed" for appointments the practice will never see.

| # | Item | Size | Owner | Notes |
|---|---|---|---|---|
| **3** | Prevent the same booking being written twice | 1d | Connector + Cloud Service | Deterministic idempotency key derived from booking content, stored in the practice DB. Root cause of the duplicate-booking class |
| **1** | Check the clinic's schedule before writing | 1.5d | Connector | Slot free / patient resolves / not already there — same connection, back to back, no long lock |
| **2** | A conflict outcome for writes that must not proceed | 1.5d | Cloud Service + Ops UI + **Platform mirror** | Conflict must be terminal, never retried. Platform side is ours |
| **4** ◐ | Tell the caller when a booking is not yet real — **Platform half done, `0d096f2`** | 0.5d | Cloud Service API + ~~Platform~~ | Ours landed first, as the doc requires. Remaining: the Cloud Service must emit `write_status`, and run-history visibility arrives with Item 11 |
| **5** | Recover in-flight writes after Connector restart | 0.5d | Connector | Blocked on Decision I (internal). Encrypt anything stored on the clinic machine |

**Our slice:** Items 2 and 4 have real Platform work — mirroring the conflict state and handling
`pending` as an outcome distinct from success and failure. Item 4's Platform half is the single
best first task in the backlog.

---

# WS2 · GoTracker operations & health
**5 items · ≈ 4.5 days · mostly Cloud Service / Ops UI**

The GoTracker path is otherwise in good shape. This is the layer that lets an operator find out
something is wrong before the clinic phones in.

| # | Item | Size | Owner | Notes |
|---|---|---|---|---|
| **8** | Mapping review before a clinic can take live bookings | 2d | Ops UI + Cloud Service + **Platform checklist** | Must refuse by default. A location can currently take live bookings the moment credentials are saved |
| **6** | Alert when a clinic's connection is unhealthy | 1d | Cloud Service + CDK | Six conditions. Data is already collected; nothing consumes it. Must not page overnight when a clinic is simply closed |
| **9** | Sign the messages the Connector sends | 0.5d | Connector + Cloud Service | Auth exists, integrity does not. Needs a dual-accept transition window or clinics drop off |
| **10** | Operator runbooks for the GoTracker path | 0.5d | Docs | Five runbooks, each tied to an alarm from Item 6 |
| **7** | Complete the connection health screen | 0.5d | Ops UI | Five missing fields over data that already exists. Cheapest item in the backlog |

---

# WS3 · Campaign engine core
**8 items · ≈ 9 days · Platform backend (+ builder UI) · TIER 1**

Where the silent failures live. Everything here is in this repo.

| # | Item | Size | Notes |
|---|---|---|---|
| **11** | A booking step inside campaigns | 3d | Hard blocker on Recall and Sales Qualification. Booking already works for the voice agent — it just isn't a campaign step. Needs booked / could-not-book / **pending** branches. Blocked on Decision B |
| **12** | Generate booking, confirmation and reschedule links | 3d | **Currently unsafe.** Six templates already use the placeholder; nothing generates the value. Needs signed, expiring, per-run links plus patient-facing pages |
| **14** ✅ | Retry text messages — **done, `7116457`** | 0.5d | Email already does this correctly — copy it. Must ship *with* the provider idempotency key or retries become duplicates |
| **15** ◐ | Delivery results into campaigns — **done bar branching, `cc3f28a`** | 0.5d | Receipts arrive and feed billing, but never reach the campaign. Undelivered currently counts as contacted |
| **16** | Stop contacting a patient on other channels once they reply | 0.5d | Today this is a property of how two campaigns were drawn, not an engine guarantee |
| **21** | A store for inbound enquiries | 0.5d | Blocks Item 24 entirely. Needs RLS isolation + idempotent intake key + encryption at the same standard as patient contacts |
| **13** | Enforce readiness checks when a campaign is published | 0.5d | The real check exists; the publish path calls a placeholder that deliberately does nothing. Sequence *after* 11 and 12 |
| **30** | Block campaigns that need data a clinic cannot provide | 0.5d | Flip warn → refuse, treat unknown as unavailable |

---

# WS4 · The four campaigns
**3 items · ≈ 10 days · Platform + campaign design · TIER 2**

Two campaigns are live and good. Two are two-step placeholders and one does not exist.

| # | Item | Size | Notes |
|---|---|---|---|
| **22** | Build out Appointment Reminder and Overdue Recall | 5d | Rebuild both to the depth of the live campaigns (~15–20 steps each). Reminder must re-check live practice data before every send. **Switching them on is the last action in this workstream.** Blocked on Decision D, needs Items 11, 12, 13, 25 |
| **24** | Build the Sales Qualification campaign | 4d | Does not exist in any form. Intake route + trigger + template + provider/type matching + patient conversion. Blocked on Decision C, needs Items 21 and 11 |
| **23** | Run Overdue Recall for GoTracker clinics | 1d | Must refuse to run on a clinic whose history sync is incomplete — otherwise it tells last month's patients they haven't been seen in two years |

---

# WS5 · NexHealth data families
**6 items · ≈ 11.5 days · Platform integration · TIER 2 — largest workstream**

Runs independently of WS3/WS4 and is the natural second developer's lane. Two of the six contracted
families are complete; four are not.

Every item here carries the same five-part definition of done: minimal field return, role-based
access control, audit on every access, per-workflow field allow-lists, and sandbox tests proving
sensitive fields are withheld. **No financial or clinical field reaches the voice agent without an
explicit per-workflow declaration.**

| # | Item | Size | Notes |
|---|---|---|---|
| **28** | Financials — charges, claims, payments, balances | 4d | **Largest data family in the scope.** Zero implementation today. Most sensitive data in the product — build the allow-listing *before* the retrieval |
| **25** ⏸ | Patient Communication — notes, documents, recalls — **DEFERRED 2026-08-30** | 3d | 1 of 7 record types built. **Highest-value item in Part 4** — Item 22's recall quality depends on it. Build this family first. Decisions F and G |
| **27** | Insurance — plans and coverage | 2d | Live hand-maintained data in production that the voice agent reads right now. **The migration must not lose it.** Decision H |
| **31** | Working Hours — reconcile the two sources | 1d | Detect divergence and surface it. Never auto-adopt either source — it would silently change what the agent offers on live clinics |
| **26** | Procedures — visit and treatment history | 1d | Currently a 5-entry extract on a legacy interface version. Unusable for recall targeting |
| **29** | NexHealth Operations — onboarding interfaces | 0.5d | The rest of this family is built. Shortens manual clinic onboarding |

---

# WS6 · Reliability & throughput
**5 items · ≈ 5 days · Platform + shared state · TIER 3**

Nothing here is user-visible. All of it decides how the system behaves on a bad day.

| # | Item | Size | Notes |
|---|---|---|---|
| **17** | Stop calling a service that is failing | 1.5d | Circuit breaker per service per clinic. State must be shared across app instances. If the shared store is down, fail open |
| **18** | Limit how fast and how many messages and calls go out | 1.5d | Per-clinic concurrent-call ceiling + per-provider send rates. **Decrement on timeout as well as on normal completion**, or a clinic silently stops being able to call |
| **20** | Quiet-hours exceptions | 1d | Date, patient and message-class exceptions. Reject an exception that would leave no permitted window *at save time* |
| **35** | Alert on campaign engine problems | 0.5d | Figures are published every minute; no alarm consumes any of them. Thresholds from staging observation, not guesses |
| **19** | Voicemail handling options | 0.5d | Two settings per campaign. Cap total dials separately, or a voicemail-only number gets dialled forever |

---

# WS7 · Security, audit & RBAC
**5 items · ≈ 5 days · Platform (+ all three systems for the review) · TIER 3**

| # | Item | Size | Notes |
|---|---|---|---|
| **40** | Respond to penetration-test findings | 2d | Unknown until the test runs. Agree intake and tracking **before** it starts |
| **32** ✅ | Record who changed a campaign — **done, `7572ca4`** | 1d | Was the only privileged area with zero audit coverage. 18 state-changing endpoints now audited with durable records; enrolment and template instantiation were gaps the doc did not name |
| **33** | Permissions for high-consequence actions | 0.5d | Four permissions. Gate write-replay and conflict-resolution *above* ordinary campaign editing |
| **34** | Record what caused each write to a practice's records | 0.5d | Trace identifier exists but never reaches the queued write. This is what makes a duplicate-booking investigation possible |
| **39** | Privacy and audit review | 1d | A review pass, not a feature. Would not pass today — run it *after* 32, 33 and 34 |

---

# WS8 · Dashboard UI & reporting
**2 primary items · ≈ 2.5 days · `nexus-dashboard-web` · TIER 3**

Only two items are *primarily* UI, but around fourteen have a dashboard slice.

| # | Item | Size | Notes |
|---|---|---|---|
| **37** | Outcome reporting — recalls booked, enquiries qualified, revenue | 1.5d | The three figures that answer "is this working?" Blocked on Decision A; needs Items 11 and 24 |
| **36** | A screen for messages that could not be delivered | 1d | Backend exists, screen does not. Retry must be permission-gated, audited, and safe to double-click |

**UI slices living inside other items:** campaign builder changes (11, 19, 22), publish-failure
surfacing (13), compliance settings (20), pre-launch checklist (8, 30, 31), run history (4, 15),
patient-facing link pages (12), permission-aware affordances (33), and the Cloud Service operator
screens (2, 7, 8).

---

# WS9 · Testing, CI/CD & documentation
**8 items · ≈ 10.5 days · all three systems · TIER 4**

| # | Item | Size | Notes |
|---|---|---|---|
| **41** | Test the practice-database write path properly | 3d | Sandbox practice DB + all twelve vendor write procedures + four failure modes. **This is the only proof that Items 1–3 actually work** |
| **46** | Automated build, migration checks and deployment | 2d | No pipeline in any of the three systems. Migration history has already forked more than once — that check belongs on the PR job. Connector installer needs code-signing |
| **42** | End-to-end test of the offline booking path | 1.5d | The product's most distinctive capability and highest-risk path, currently untested end to end |
| **38** | Disaster recovery procedures | 1d | In-progress campaign runs are the hard part, not the database — a restore must not re-contact patients or re-write appointments |
| **45** | Load testing | 1d | ⚠️ **Never run this locally** — use a throwaway prod-sized environment. Feeds the thresholds for Items 6 and 35 |
| **43** | Test each new data family against the sandbox | 1d | Part of each family's definition of done, not a follow-up |
| **47** | Guides for clinic administrators | 0.5d | Three guides, plain language. Connection-health guide depends on Item 7 |
| **44** | Prove clinic data isolation for the new data families | 0.5d | Extend the existing suite as each family lands |

---

# Sorted by size — build effort

Nothing in the backlog is longer than a week of building. Note how flat this ranking is: that
flatness is the point — the code stopped being the bottleneck, so the gates below decide the
schedule instead.

| Rank | # | Item | Days | Workstream |
|---|---|---|---|---|
| 1 | **22** | Reminder + Overdue Recall campaigns | 5 | Campaigns |
| 2 | **24** | Sales Qualification campaign | 4 | Campaigns |
| 3 | **28** | Financials data family | 4 | NexHealth |
| 4 | **25** | Patient Communication data family | 3 | NexHealth |
| 5 | **11** | Booking step inside campaigns | 3 | Engine |
| 6 | **12** | Booking / confirm / reschedule links | 3 | Engine |
| 7 | **41** | Practice-database write-path tests | 3 | Testing |
| 8 | **8** | Mapping review + go-live gate | 2 | GoTracker ops |
| 9 | **27** | Insurance data family | 2 | NexHealth |
| 10 | **46** | CI/CD across three systems | 2 | Delivery |
| 11 | **40** | Pentest remediation | ~2 | Security |
| 12 | **1** | Pre-write schedule check | 1.5 | GoTracker safety |
| 13 | **2** | Conflict outcome | 1.5 | GoTracker safety |
| 14 | **17** | Circuit breaker | 1.5 | Reliability |
| 15 | **18** | Concurrency + rate limits | 1.5 | Reliability |
| 16 | **37** | Outcome reporting | 1.5 | Dashboard |
| 17 | **42** | End-to-end offline booking test | 1.5 | Testing |

---

# What doesn't compress

Agents collapse the writing. They do nothing to the seventeen items below, which wait on a person,
an environment, or the calendar. Start these clocks now and the ~63 build-days fit underneath them;
leave them and they become the critical path.

| Gate | Items | Why it doesn't compress |
|---|---|---|
| **Nine product decisions** | 4, 5, 11, 22, 24, 25, 27, 37 | Decisions A–I; six need the client. **B is urgent** — it blocks Item 4 in the first stage and Item 11 in the third. Parallelism does not route around an unanswered question |
| **A practice-DB sandbox that doesn't exist** | 41, 42 — and 1, 3, 5 depend on it | No seeded GoTracker database to test writes against. The Tier 0 protections can be written but not proven, and proving them is the point |
| **Staging observation windows** | 6, 35 | Both specify thresholds from real observed values. The alarms are an afternoon; the numbers in them need days of staging traffic |
| **An unscheduled pentest** | 40 | Remediation cannot start before the test runs. Book it now and findings land while other lanes build |
| **Another team's codebase** | 19 items | Cloud Service / Connector work moves at their pace. Item 4 has a hard ordering constraint across the boundary — our side first |
| **Rollouts that must be watched** | 9, 22, 27, 38, 45 | Connector fleet update window, switching campaigns on for real clinics, migrating live insurance answers, a DR rehearsal, a throwaway prod-sized load environment. Elapsed time by nature |

---

# Sorted by importance — the tiers

**Tier 0 · Stops active harm.** Items **3, 1, 2, 4, 5**. The software can today double-book a live
clinic's schedule and tell a patient an appointment is confirmed when the practice will never see it.
Nothing else competes with this.

**Tier 1 · Silent failures and hard blockers.** Items **12, 32, 14, 15, 6, 35, 11, 30, 16, 13**.
Things that fail without telling anyone, plus the two blockers (11, 12) that half the remaining
feature work sits behind.

**Tier 2 · Contracted features not yet built.** Items **25, 26, 22, 23, 21, 24, 27, 28, 31, 29**.
Two campaigns and four NexHealth data families. The largest volume of work, and the most visible to
the client.

**Tier 3 · Operator tooling and resilience.** Items **7, 8, 36, 33, 34, 17, 18, 20, 19, 9, 37**.
Makes the product supportable and survivable rather than merely functional.

**Tier 4 · Proof, operations and documentation.** Items **41, 42, 45, 46, 10, 47, 38, 39, 40, 43,
44**. Note that 41 and 42 are the *only* evidence Tier 0 actually works — they are last in sequence,
not last in importance.

---

# Flags before we start

**1 · Decision B blocks Tier 0 and Tier 1.** When a GoTracker booking is accepted but not yet
written, does the campaign pause and wait, or exit and hand the patient to staff? It is a client
decision and it blocks Items 4 and 11 — one in the first stage, one in the third. Raise it now,
not when we reach it.

**2 · A fifth of the backlog is in someone else's codebase.** Items 1, 3, 5, 7, 9, 10 are pure
Cloud Service / Connector work, and 2, 4, 6, 8, 34, 42 are split. That coordination — particularly
the interface change in Item 4, where our side must land first — needs a named owner on both sides.

**3 · Fourteen items touch a dashboard that is being refactored right now.** Codex is mid-flight
replacing `components/ui/*` with `components/foundation/*`. Any new screen built against the old
primitives will conflict. Either sequence UI work behind that refactor or build against the new
foundation from the start.

**4 · Three items carry a migration or live-data hazard.** Item 27 (insurance answers the voice
agent reads today), Item 31 (silently changing offered slots on live clinics), and Item 22
(switching campaigns on before links and booking exist).

**5 · Item 45 must not run locally.** Load testing goes on a throwaway prod-sized environment.

---

# Suggested first moves on this branch

Everything below is Platform-side, in this repo, unblocked, and in dependency order.

1. **Item 4 — Platform half.** Handle `pending` as an outcome distinct from success and failure, and
   fix the message that always claims success. The doc explicitly requires our side to land before
   the Cloud Service starts sending the status. *(Confirm Decision B first — it shapes the campaign
   behaviour, though the plumbing can be built either way.)*
2. **Item 32 — campaign audit.** No dependencies, closes a genuine compliance gap, pure backend.
3. **Item 12 — link generation.** Currently unsafe, and blocks Items 13 and 22.
4. **Item 14 + 15 — text retry and delivery receipts.** Build 14 with the provider idempotency key
   in the same change; 15 is small and independent.
5. **Item 25 — Patient Communication family.** Start the second lane here if there is a second
   developer; it is the prerequisite for Item 22 being any good.
