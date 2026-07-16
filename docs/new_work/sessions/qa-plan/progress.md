# Progress: QA execution

## 2026-07-13 — Layer 0 + Layer 1 executed

### Layer 0 — Environment
- 0.1 ✅ `make up-deps` — Postgres :5433 + Redis :6379 up (docker compose).
- 0.2 ✅ Fresh migration to head — **found + fixed a real bug**: the SMS migration's revision id
  `20260712_sms_workflow_attribution` was **33 chars > alembic_version varchar(32)**. Never surfaced
  before because it was only offline-checked (`alembic heads`) + unit-tested, never *applied*. First
  real apply failed on the version stamp. Shortened to `20260712_sms_wf_attribution` (27), renamed the
  file, re-applied clean. **This is a fix to an already-committed migration → needs a follow-up commit.**
- 0.3 ✅ `alembic current` == `heads` == `20260712_sms_wf_attribution` (single head).
- 0.4 ☐ FE `node_modules` ownership — **BLOCKED, needs `sudo chown` (user).**
- 0.5 ☐ Vendor sandbox creds — user/ops.
- 0.6 ☐ Seed tenant — pending (needed for Layer 2).

### Layer 1 — Automated regression (backend GREEN)
- 1.1 ✅ Backend unit: **1482 passed, 0 failed** (with DB+Redis up, the 3 previously-Redis-down
  appointment tests now pass).
- 1.1b ✅ Installed missing `respx` dev dep → the 2 previously collection-broken files
  (`test_locations_routes`, `test_nexhealth_client`) now pass (7). **Full unit suite is now green with
  zero exclusions.**
- 1.2 ✅ Integration (real Postgres, testcontainers): **12/12 passed** — migration chain + engine +
  voice on a fresh DB (5m26s).
- 1.3 ✅ RLS / tenant-scope invariant + RBAC matrix green (part of the 1482).
- 1.4 ✅ Frontend (after user ran `chown` + `npm install`, 484 pkgs): **tsc clean (exit 0)** —
  DNC UI + Plan 08 fix + builder all typecheck. **vitest: 140 passed / 23 files** (with
  `--testTimeout=20000`). My changes verified: `do-not-contact-api` + `automation-api` 10/10.
  NOTE: default 5s timeout causes cold-run flakiness on 4 WorkflowBuilder.publish tests under parallel
  load (all pass in isolation / with a higher timeout) → tiny follow-up: bump `testTimeout` in vitest config.

### Net
Backend QA baseline is fully green. Remaining to start Layer 2/3 is user/ops-dependent:
`sudo chown node_modules`, vendor sandbox creds, seed a tenant. Then drive the Layer 2 E2E flows.

### Layer 2 — Local E2E backend (durable path chosen: real-Postgres integration tests)
Chose Option B (extend the integration suite) over fragile hand-driven curl E2E — the engine core
was already covered by the 12 integration tests; the gap was the channel/webhook/gate paths (mock-only).
- ✅ NEW `tests/integration/test_outbound_channels_integration.py` — **4 passed** (real Postgres,
  vendors stubbed at the HTTP boundary), independently re-run confirmed:
  1. SMS send E2E → `sms_history_logs` carries `workflow_run_id` + `workflow_id` (Fix 2, Plan 04/11).
  2. Compliance gate blocks on DNC → run FAILED `compliance_blocked`, no send attempted (Plan 12).
  3. Email suppression by `email_hash` → REVOKED EMAIL consent → gate blocks subsequent email (Fix 1, Plan 05).
  4. Voice metadata carries `workflow_id` (Fix 2, Plan 03/11).
- **No bugs found** — all three shipped fixes behave correctly against real Postgres.
- Note: test 3 exercises the service method (`record_email_consent_identity`) not the Celery `.delay`
  fan-out wrapper (which opens its own global-engine session; covered by unit tests).

### Layer 2 verdict
Backend E2E for the engine (12 tests) + channels/gate/fixes (4 tests) is now durable + green.
Remaining Layer 2 breadth (more channels/edge cases) can grow the same file over time.

### Live API smoke (bridge check — 2026-07-14)
Brought up the API container (`docker compose up -d api`); `/livez` → 200 after 18s.
`POST /api/auth/login` (inst.admin@bright-smile-dental.dev / LocalDev123!) → HTTP 200
`{"status":"mfa_setup_required", ...}`. **This is correct behavior** and validates the live stack:
app boots, DB connected, seeded user resolves, auth works, MFA is enforced. Did NOT script through
the TOTP-setup dance to a token — the authenticated enroll→advance path is already proven by the
real-Postgres integration tests, so the marginal value is low. **API container left running for the
deferred Layer 3 manual FE pass.**

### Remaining layers (increasingly user/ops-dependent)
- **Layer 3 — Frontend manual click-through**: needs app up + browser. FE now builds (140 vitest green);
  the manual pass validates DNC UI / Campaign UI / Builder against a live backend.
- **Layer 4 — Real vendors**: needs full sandbox creds (Twilio auth token, `RESEND_WEBHOOK_SECRET`) —
  answers the open "does Resend echo tags?" question + deliverability + NexHealth staging (Plan 09 runbook).
- **Layer 5 — Prod canary**: feature-flagged, one clinic, watch observability.

### Follow-up commit needed
- `alembic/versions/20260712_sms_wf_attribution.py` (renamed + revision id shortened) — fixes the
  33-char revision id that breaks `alembic upgrade head`.

## 2026-07-15 — Layer 4/5: Plan 09 live sandbox verification (see plan-09-staging-results.md)
Ran Plan 09 flows against the real NexHealth sandbox (`silora-demo-practice`, loc 348511). Discovered the
value the CTO gave was the **API key** (not a webhook secret); `.env` corrected. All 4 flows verified live;
**found + fixed 3 real bugs the mock tests missed:**
- Flow 2 backfill: `GET /appointments` `start_date/end_date` → **`start/end`** (400 otherwise). Fixed.
- Flow 3 subscription: reworked from the dead `/webhooks` (404) to the real **`/webhook_endpoints`** 2-step
  form flow; valid events `appointment_insertion`/`appointment_updated`; endpoint-create returns the signing `secret_key`.
- Flow 4 inbound: reworked parser to `event_name` (+ `.complete` normalize) and the
  `HMAC(secret_key, "{timestamp}.{base64(body)}")` scheme via `signature`/`timestamp` headers — **verified live**
  with a real endpoint secret (valid accepted, tampered/missing-ts → 403).
All v2.0-era-code vs current-v2 (v2.2.2) drift, not v3. **180 NexHealth/Plan-09 tests pass.** Plan 09 ~80% → **~95%**
(full real-appointment round-trip pending — empty sandbox tenant).
> **SUPERSEDED (2026-07-15):** the round-trip is now DONE — **Plan 09 = 100%.** See the next entry below.

## 2026-07-15 — Layer 4/5: Plan 09 REAL webhook round-trip ✅ DONE → Plan 09 = 100% (see plan-09-staging-results.md, Flow 5)
Closed the last ~5%. Over a **cloudflared** quick tunnel (ngrok is unusable in this env — CRL fetch fails),
drove a genuine end-to-end round-trip against the sandbox:
- Registered a real endpoint (`POST /webhook_endpoints` → 201, id 16271, real `secret_key`) + subscriptions
  `appointment_insertion`/`appointment_updated`. Set `NEXHEALTH_WEBHOOK_SECRET` and **recreated** the api
  container (`compose up --force-recreate`; `docker restart` does NOT reload `env_file`) so signature verify
  actually runs. Temporarily wired local "Downtown Clinic" → loc 348511.
- **Booked** appt 1599668687 → real `appointment_insertion` webhook → 200 OK: sig verified, event parsed,
  location resolved, ledger `COMPLETED`, `appointment_working_set` upserted (`scheduled`), workflow enqueued.
- **Cancelled** it → real `appointment_updated` webhook → 200 OK: projection flipped to `cancelled`.
- Both insert + update/cancel paths proven against genuinely NexHealth-signed deliveries.
- Cleanup: appointment cancelled, endpoint deleted (204), secret cleared + container recreated, location
  reverted to NULL, local test rows purged, tunnel stopped. `git status` clean (docs only).
**The "empty tenant" blocker was resolved by booking a bookable provider/type/operatory. Plan 09 = 100%.**

## 2026-07-15 — Layer 6: Frontend panel verification (browser walk-through)
Ran the dashboard (`nexus-dashboard-web`, Vite/React) at localhost:3000 against the local API + seeded DB.
Seeded 4 demo campaigns for `bright-smile-dental` (all Plan 06 templates) via the definition service so the
Plan 02/08 screens have real data. Verified route map + RBAC + login/MFA (TOTP). UI plans in scope: **02**
(workflow builder), **08** (campaign mgmt), **11** (usage/cost cards + group rollup), **12** (DNC), plus
settings (10/05) + callbacks (07). Voice (03) has no UI by design.

**QA gap found + fixed — Workflow builder (Plan 02): palette had no drag-to-add.**
- **Gap:** the palette was **click-to-add only** (`WorkflowPalette` used `onClick` buttons). New nodes are
  unconnected, so the deterministic layered layout parks them in a **trailing column stacked off-screen**
  (`graph.ts computeDepths` → `maxDepth+1`), making it feel like "nothing happened, just a config panel opened."
  Functionally it worked; discoverability was poor. No on-canvas delete/keyboard-delete either (delete lives as
  the "Delete step" button in the config panel — accepted as-is by the reviewer).
- **Fix (frontend, presentational only — no execution-semantics change):** added **drag-to-add**. Palette items
  are now `draggable` and set a typed DataTransfer MIME (`WORKFLOW_NODE_DND_MIME` in `lib/workflow/catalog.ts`);
  the canvas wires `onDragOver`/`onDrop` and uses React Flow `screenToFlowPosition` to drop the node **under the
  cursor**, pinning it into `def.layout` via the existing `setNodePosition`. Click-to-add kept as a fallback.
  Files: `catalog.ts`, `WorkflowPalette.tsx`, `WorkflowCanvas.tsx`, `WorkflowBuilder.tsx`.
- **Verified:** `tsc --noEmit` clean; workflow FE tests green (WorkflowBuilder.render/publish + WorkflowTemplates,
  7 tests). Manual re-verify by the reviewer pending.

## 2026-07-15 — Layer 6b: Automated headless-browser verification (Playwright) ✅ ALL PASS
No Playwright MCP is connected (MCP needs a Claude Code restart to load), so ran a **headless Playwright script**
against the live app (host node v22; `/tmp/pw-verify/verify.mjs`). Login is MFA-gated: scripted API-driven login
that computes the TOTP with node:crypto (SHA1/6/30) off the `setup/options` secret, then relied on the HttpOnly
refresh cookie + the app's boot-refresh to hydrate the session (access token is in-memory only — `token-manager.ts`).
Used **fresh (no-MFA) accounts** so as not to touch the reviewer's `bright-smile-dental` admin (which now has a
real TOTP factor); seeded 4 campaigns into `lakeview-orthodontics` for the inst-admin run.

Results (10 screens + 3 role logins, screenshots in `/tmp/pw-verify/shots/`):
- **login** inst-admin / group-admin / staff — all PASS (TOTP enrolled programmatically).
- **01 campaigns list** (Plan 08) — all 4 campaigns render.
- **02 campaign detail** (Plan 08+11) — run/usage/cost/enroll/halt/pause controls present.
- **03 builder** (Plan 02) — 6 draggable palette items, 3 canvas nodes.
- **04 drag-to-add** (the fix) — **synthetic DnD drop added a node, canvas 3→4** ✅ (screenshot shows a new Wait node).
- **05 versions** (Plan 02) — version history renders.
- **06 do-not-contact** (Plan 12), **07 settings** (Plan 10/05: email/from/address), **08 callbacks** (Plan 07) — all render.
- **09 group dashboard** (Plan 11 rollup) — renders for GROUP_ADMIN.
- **10 RBAC** — STAFF hitting `/institution-admin/campaigns` is redirected to `/dashboard` (blocked) ✅.
- **No real API errors** — only the expected boot 401 (pre-refresh) and an SSE (`/institution/events`) stream
  aborting on navigation; both cosmetic.
- **Cleanup:** deleted the TOTP factors + recovery codes enrolled for the 3 test accounts (reset to 0 / fresh).
  Left seeded campaigns (bright-smile-dental + lakeview-orthodontics) in place as demo data.
**Frontend UI (Plans 02/08/11/12 + 10/05/07) verified end-to-end in a real browser. drag-to-add confirmed working.**

## 2026-07-15 — Layer 6c: EXHAUSTIVE per-control Playwright pass + a real bug
Deep script (`/tmp/pw-verify/deep-verify.mjs` + `builder2.mjs`) clicking individual controls on a throwaway
"QA Sandbox" campaign (lakeview) so mutations/destructive actions didn't touch demo data.

**Verified working (per-control):** builder toolbar (Versions/Test run/Pause-Resume/Archive/Publish/Tidy),
drag-to-add, **content-class select**, **consent toggle**, validation shows errors + **Publish correctly disabled
while errors exist**, tidy layout, **test-run dialog opens**, versions page, pause/resume (toasts). Campaigns list:
New-from-template, Refresh, outbound-halt dialog (cancelled). Detail: usage cards 5/5, Enroll dialog, Halt dialog
(cancelled). Settings: Save billing, New transfer Row, Save ROI config. Callbacks: filters, Reveal, Resolve dialog.
Group: controls + practice filter. **DNC add opt-out works** (valid phone → 201) + list renders.

**Could NOT cleanly auto-confirm (Playwright actionability quirk on React-Flow palette/nodes — NOT proven broken;
the handlers are proven via drag-to-add which adds nodes, and the user manually confirmed click-to-add):**
click-to-add per node type, per-node StepConfigPanel fields (SMS/email/voice/wait/condition/exit), edit-message,
delete-step. `.click()` on `button[draggable=true]` / `.react-flow__node` timed out — a test-harness limitation.

**🐞 REAL BUG FOUND — Do-Not-Contact add: 500 on invalid phone.**
- `POST /api/institution/do-not-contact` with an **invalid** phone (e.g. a `555` fake number) → **500 Internal
  Server Error** (unhandled `ValueError("Recipient phone number is required")` at `sms_compliance.py:68`, via
  `hash_phone` returning empty for an unparseable number; route `do_not_contact.py:88` doesn't catch it).
- Frontend surfaces it as an opaque **"Network Error"** (the 500 response is CORS-blocked from the browser).
- **Happy path works:** valid numbers (`+12128675309`, `+442071838750`) → **201 Created**.
- **Severity: low–med** (bad UX on bad input; not a crash of the happy path). **Fix:** catch `ValueError` in the
  route → return 422 with a helpful message, and/or client-side validate the phone before submit.

**Possible (unconfirmed) — Publish via UI:** publishing after toggling *consent_required* off returned
"Failed to publish — the server rejected the definition." Likely **correct server-side guarding** (SMS without
consent), not a confirmed bug — needs a deliberate check.

**Cleanup:** MFA factors reset (0) on all test accounts; QA Sandbox deleted; test DNC rows deleted; lakeview
billing email reverted; 4 lakeview demo campaigns left in place. No code changes this round (verification only).

## 2026-07-16 — Layer 6d: Workflow builder UX polish (5 reviewer-requested fixes)
1. **Drag-drop no longer opens the config panel** — `onAddNodeAt` (WorkflowBuilder.tsx) no longer calls
   `onSelect(newId)`; dropping a node just places it.
2. **Removed click-to-add** — palette items are now **drag-only** (`WorkflowPalette.tsx`: dropped the `onClick`/
   `onAddNode` wiring; items are `<div role=button draggable>` with a "Drag onto the canvas" title). Removed the
   now-unused `onAddNode` from WorkflowBuilder.
3. **Config panel opacity** — `StepConfigPanel` `SheetContent` was translucent because the shared `sheetVariants`
   uses `bg-gradient-to-b from-background to-accent/30` (lower half faded). Overrode with a solid
   `[background:hsl(var(--background))] shadow-2xl` on the builder's panel.
4. **Zoom controls visible** — React-Flow `Controls` rendered as a white strip (xyflow's dark palette is gated
   behind OS `prefers-color-scheme`, but the app forces dark via a `.dark` class → it got the light `#fefefe`
   defaults). First CSS attempt lost to xyflow's stylesheet (imported after `index.css`); fixed by overriding
   both the xyflow CSS vars (`--xy-controls-button-*`) and the resolved styles with **`!important`** in
   `index.css`. Now a dark strip with legible +/−/fit icons (screenshot-confirmed).
5. **Removed the minimap** — dropped `<MiniMap>` (import + JSX) from `WorkflowCanvas.tsx`.
Verified: `tsc --noEmit` clean; 9 workflow FE tests pass; Playwright screenshots confirm opaque panel, visible
zoom controls, and no minimap. Files: WorkflowBuilder.tsx, WorkflowPalette.tsx, StepConfigPanel.tsx,
WorkflowCanvas.tsx, index.css.

## 2026-07-16 — Layer 6e: FIX — "Publish changes" on an active campaign (was 409 → misleading error)
**Bug:** editing a **live (active)** campaign and clicking "Publish changes" failed with a **409 Conflict**
(`definition_service.py:142` — `_PUBLISHABLE_STATUSES` was `{draft, paused}`, excluding active), which the UI
showed as the misleading *"Failed to publish — the server rejected the definition."* Client `/validate` returned
200 (definition was valid); the 409 was a **status** conflict, not a definition problem. Since the builder treats
active campaigns as editable/publishable, this broke publishing edits to every live campaign.
**Fix A (backend):** added `ACTIVE` to `_PUBLISHABLE_STATUSES` → publishing a live campaign snapshots a new
version and stays active (hot-swap). In-flight runs pin their version at enroll time, so they keep the old
definition; new enrollments use the new one. **Fix C (frontend):** `WorkflowBuilder.tsx` publish `catch` now
surfaces the server's real `detail` (`Couldn't publish: …`) instead of the generic message.
**Verified:** service-layer test — publishing an ACTIVE workflow bumps version 1→#2, stays active, no 409;
57 backend unit tests pass; frontend `tsc` clean. Files: `definition_service.py`, `WorkflowBuilder.tsx`.
