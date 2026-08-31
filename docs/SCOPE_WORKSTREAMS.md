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
| **12** · Generate the three link types | Campaign engine core | `797063d` + `e43378d` | Signed, run-scoped, expiring tokens (action and expiry both inside the signature) plus the three public landing endpoints. Confirm completes the write-back; book and reschedule capture intent and raise a staff handoff, because no patient-facing slot picker exists. **Remaining: a booking page**, so those two can finish unattended |
| **21** · Inbound enquiry store | Campaign engine core | `080e3a0` | `campaign_enquiries` with RLS (forced, `WITH CHECK` so a clinic can't insert into another's scope), unique on `(institution_id, intake_key)` for idempotent resubmission, AES-GCM email/phone with the hash written by the same setter. Unblocks Item 24 |
| **30** · Block unsupported campaigns | Campaign engine core | `d3c444b` | Doc said flip warn→refuse; instantiation already refused. Real gap: requirements lived on template metadata and never reached the definition, so publish never re-checked. Now carried and re-evaluated; unknown still counts as unavailable |
| **13** · Enforce readiness at publish | Campaign engine core | `417bf54` | Doc was out of date — the publish path already ran the real readiness service, fail-closed. Real gap: SMS-not-provisioned was a *warning* while voice was an error, so a campaign published for a clinic with no Twilio sender and failed for every patient. Email's platform-address fallback stays a warning, since that mail does deliver |
| **16** · Cross-channel suppression | Campaign engine core | `46a3a9f` + `fca9d07` | Moved from "how the campaign was drawn" to an engine rule in the compliance gate, checked before quiet hours. Opt-out moved to the send node (`send_after_response`) after `fca9d07` — on `ComplianceMetadata` it was dead, since `publish_version` strips that block. Verified safe for both live campaigns — they hold only a voice attempt ladder, no post-response sends |
| **3** · Prevent the same booking being written twice | GoTracker booking safety | `e294420` | Connector asks the chart "did I already write this?" before every write, keyed on `CreatedUserId='ThirdPartyIntegrator'`. A retry returns the existing id and writes nothing. Root cause of the duplicate-booking class |
| **1** · Check the clinic's schedule before writing | GoTracker booking safety | `b066a8d` | Patient-resolves + slot-free re-checked on the same connection immediately before the write. Cancelled statuses release their slot; touching boundaries are not overlaps |
| **2** · A conflict outcome for writes | GoTracker booking safety | `4adc67c` | Third terminal status — never re-queued however many attempts remain. Own webhook action. Surfaced per-location on `/api/admin/sync_status` as `conflicts` / `failed` / `oldest_unwritten`. An admin can still re-queue deliberately |
| **4** · Report pending honestly | GoTracker booking safety | `0d096f2` + `af01334` | Platform half landed first, as the doc requires. Booking response and appointment reads carry `write_status` (`pending`/`written`/`failed`/`conflict`) + `foreign_id`, separate from `status`; PMS-origin rows read as `written`. Run-history visibility still rides with Item 11 |
| **5** · Recover in-flight writes after Connector restart | GoTracker booking safety | `305ed2d` | Item 3's read-back applied to patient creation too. No local state, so **Decision I fell away instead of being answered** — the decision log's recommendation was to avoid a durable local record, and that is what shipped |

---

## Fixes found along the way

Not scope items, but they blocked or silently defeated scope work.

| Fix | Commit | Why it mattered |
|---|---|---|
| Migration chain replayable on a fresh database | `fa27614` | `alembic upgrade head` could not build a database from scratch, so **no new environment could be stood up and the entire RLS tier was unrunnable** — which is where Item 44's isolation coverage has to live. Four guards; a fresh build now matches dev exactly (87 tables, 559 indexes, 95 policies) and the RLS suite passes 11/11. Models and schema were verified to agree exactly — this was never drift |
| Security middleware no longer downgrades `Referrer-Policy` | `527be8b` | It overwrote the link endpoints' `no-referrer` with `strict-origin-when-cross-origin`, which still sends the full URL — token included — same-origin. Item 12's Referer defence was **absent in the running app**; the endpoint tests mount a bare router and never saw the middleware |
| `channel` value the response table permits | `dc74076` | The link endpoints wrote `"link"`, which the CHECK constraint rejects. Every confirm and handoff would have failed on insert; mocked sessions never touched the constraint |
| Enquiry column types + duplicate index | `f358857` | `String(36)` ids against `uuid` targets, and a model declaring two indexes of one name |
| Cross-channel suppression opt-out moved to the send node | `fca9d07` | On `ComplianceMetadata` it was dead code — `publish_version` strips that block, so the flag could never be switched on |

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

Was the highest-priority group in the whole backlog, and the only one where the software could
cause real-world harm with no error to warn anyone: double-booked slots in a live practice, and
patients told "confirmed" for appointments the practice will never see. Shipped as backend
`20260831-af01334` on staging and production, agent `2.1.8` (staging 100%, production registered at
0%); 386 backend and 157 agent tests at the close.

| # | Item | Size | Owner | Notes |
|---|---|---|---|---|
| **3** ✅ | Prevent the same booking being written twice — **done, `e294420`** | 1d | Connector + Cloud Service | Doc specified a deterministic key derived from booking content. What shipped reads back instead: the Connector asks the chart "did I already write this?", keyed on `CreatedUserId='ThirdPartyIntegrator'`, so a retry returns the existing id and writes nothing |
| **1** ✅ | Check the clinic's schedule before writing — **done, `b066a8d`** | 1.5d | Connector | Patient-resolves + slot-free re-checked on the same connection immediately before the write. Cancelled statuses release their slot; touching boundaries are not overlaps |
| **2** ✅ | A conflict outcome for writes that must not proceed — **done, `4adc67c`** | 1.5d | Cloud Service + Ops UI + **Platform mirror** | Terminal, never re-queued however many attempts remain, with its own webhook action. Surfaced per-location on `/api/admin/sync_status` as `conflicts` / `failed` / `oldest_unwritten`; an admin can still re-queue deliberately |
| **4** ✅ | Tell the caller when a booking is not yet real — **done, `0d096f2` + `af01334`** | 0.5d | Cloud Service API + Platform | Ours landed first, as the doc requires. Booking response and appointment reads now carry `write_status` (`pending`/`written`/`failed`/`conflict`) + `foreign_id`, separate from `status`. PMS-origin rows read as `written`. **Remaining: run-history visibility, which arrives with Item 11** |
| **5** ✅ | Recover in-flight writes after Connector restart — **done, `305ed2d`** | 0.5d | Connector | Doc expected a durable local record and Decision I to settle it. Items 3 and 5 converged on one mechanism instead — read back from the chart — so there is no local state, nothing to encrypt, and **Decision I never needed answering** |

**What this closes and what it does not.** The double-booking and false-confirmation classes are
both gone. Two things ride on elsewhere: **run-history visibility** for `pending` / `conflict`
arrives with Item 11 in WS3, and **proof** — Items 41 and 42 are still the only end-to-end evidence
the write path holds against a seeded practice database, and that sandbox does not exist yet. The
protections ship with their own coverage; the write-path suite remains outstanding. Item 7 in WS2
is now fully unblocked, since the conflict count it was waiting on shipped with Item 2.

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
| **12** ◐ | Generate the three link types — **tokens + endpoints done, `797063d` `e43378d`** | 3d | Five templates use the placeholders (all switched off), and nothing generated a value. Signed run-scoped expiring tokens + three public endpoints now exist; confirm completes the write-back. **Remaining: a patient-facing slot picker**, so book and reschedule finish unattended instead of raising a staff handoff |
| **14** ✅ | Retry text messages — **done, `7116457`** | 0.5d | Email already does this correctly — copy it. Must ship *with* the provider idempotency key or retries become duplicates |
| **15** ◐ | Delivery results into campaigns — **done bar branching, `cc3f28a`** | 0.5d | Terminal receipts now mark the step `sent:delivered` / `sent:undelivered`, so reporting tells arrival from acceptance. **Remaining: letting a campaign branch on a hard delivery failure** — by the time a receipt lands the run has usually advanced past the step, so it needs run-state work |
| **16** ✅ | Cross-channel suppression — **done, `46a3a9f`** | 0.5d | Today this is a property of how two campaigns were drawn, not an engine guarantee |
| **21** ✅ | Inbound enquiry store — **done, `080e3a0`** | 0.5d | Blocks Item 24 entirely. Needs RLS isolation + idempotent intake key + encryption at the same standard as patient contacts |
| **13** ✅ | Enforce readiness at publish — **done, `417bf54`** | 0.5d | Doc was out of date: publish already ran the real check, fail-closed. The gap was SMS-not-provisioned being a *warning* while voice was an error |
| **30** ✅ | Block unsupported campaigns — **done, `d3c444b`** | 0.5d | Doc said flip warn→refuse; instantiation already refused. The gap was requirements living on template metadata and never reaching the definition, so publish never re-checked |

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
