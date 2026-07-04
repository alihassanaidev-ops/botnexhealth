# Findings — Plan 03 Voice UI/API + closeout

**Date:** 2026-07-04 · **Branch:** `ali/phase-2` (@ `d710e11`) · **Alembic head:** `20260708_voice_data_model`
**Method:** 4 parallel graphify-first research agents (backend API/RBAC, FE structure, webhook, V-5/V-9 boundaries),
cross-referenced to code + docs (file:line below).

## F-1 — Backend API conventions (for V-8 endpoints)
- Routers: `src/app/api/routes/*.py`, module-level `APIRouter(prefix="/<resource>", tags=[...])`; registered manually in
  `src/app/main.py` with app prefix `/api` (business routers) — e.g. `/api/automation/workflows`
  (`automation_workflows.py:34`, `main.py:271`). A module can export multiple routers (`sms.py` admin vs institution).
- Schemas: **inline Pydantic** at top of the router module; naming `XxxCreateRequest/UpdateRequest/Response`; responses have
  a `@classmethod from_model` that stringifies UUIDs (`automation_workflows.py:50-116`).
- **Tenant scope from auth, never body:** `_institution_id(user)` (403 if absent); `location_id` may appear in a body for
  location-targeted resources. RLS enforces isolation at the DB layer; routes add `.where(institution_id==inst)` as defense.
- **RBAC = role-based FastAPI deps, NO permission-enum** (`src/app/api/deps.py`): `get_current_institution_or_location_admin`
  (writes), `get_current_institution_or_location_user` (reads incl. STAFF), `get_current_institution_admin`, etc. Roles in
  `UserRole` (`models/user.py:21`). Routers alias as `Annotated[User, Depends(...)]`.
- Session: `async with get_db_session()` (auto commit/rollback + RLS clear on exit); RLS context set by `get_current_user`
  → `set_current_rls_context` → `apply_rls_context` (`database.py:159-173, 289-311`; `deps.py:74`).
- **Drill-down template exists:** `GET /{workflow_id}/runs` + `/runs/{run_id}` (`automation_workflows.py:604-643`) —
  filter by institution, `order_by(created_at.desc())`, `limit=Query(50, ge=1, le=500)`. Mirror this for voice attempts.
- **Gap:** NO HTTP routes expose `outbound_voice_profiles` or `workflow_voice_attempts` yet. The recorder
  (`voice_attempt_recorder.py`) owns all writes; add a `list_voice_attempts(...)` read helper there to keep one seam.

## F-2 — Frontend (nexus-dashboard-web): API-first is right
- Vite + React 19 + react-router-dom v7 SPA; shadcn/ui; axios client `src/lib/api.ts` + per-domain wrappers
  (`automation-api.ts`, `calls-api.ts`); **no react-query** — manual `useState/useEffect` + `sonner`.
- A **mature campaign/automation surface exists** (`Campaigns.tsx`, `CampaignDetail.tsx` runs table, `WorkflowBuilder`),
  and voice is already a builder step type — but there is **no voice-profile UI and no voice-attempt drill-down** (greenfield,
  yet with near-exact templates).
- Natural homes: `LocationAdminPanel.tsx` (per-location, `LOCATION_ADMIN`) for the profile editor; `CampaignDetail.tsx`
  runs table + **`RevealablePhone.tsx`** (masked-by-default, audited reveal — reusable) for the attempt drill-down.
- **Scope: SMALL–MEDIUM FE, but the load-bearing/decision-heavy work is the BACKEND API** (RBAC, RLS, schemas, reveal
  auditing). Nothing is queryable/editable over HTTP today, so the FE can't be built or tested against anything.
  **→ Recommendation: API-first.** Freeze the endpoints + response shapes; FE is a fast, low-risk follow.

## F-3 — disconnection_reason threading (small follow-on, LOW risk)
- Webhook `process_retell_call_analyzed_event` (`retell/webhooks.py:309`); outbound enqueue block `:511-540`. Raw
  `event.call.disconnection_reason` read at `:525`, mapped via `voice_outcome.map_disconnection_reason` `:524-526`, then
  `resume_voice_outcome.apply_async(kwargs={institution_id, retell_call_id, call_outcome})` `:527-534`. Raw value **not** passed.
- `stamp_attempt_outcome` **already accepts** `disconnection_reason` (optional) and writes it when non-None
  (`voice_attempt_recorder.py:162,179-180`) — **no recorder change needed**.
- **Only one enqueue caller** (the webhook). Adding an optional kwarg breaks nothing (old in-flight messages default None).
- **Minimal change (3 edits):** webhook adds `disconnection_reason=event.call.disconnection_reason` to the kwargs; the task
  `resume_voice_outcome` + `_resume_voice_outcome_async` thread an optional `disconnection_reason` param into the existing
  `stamp_attempt_outcome(...)` call. Raw value already persisted on the Call row (`:393`) → no new PHI surface.

## F-4 — V-5 & V-9 correctly deferred (do NOT build here)
- **V-5 (voice metering):** an explicit **Plan 11** deliverable (11-doc §scope/data-model: "Retell connected minutes, dials";
  `usage_events` covers `channel=voice`; Plan 11 hooks the Retell webhook to emit). Plan 03 lists metering only as an upstream
  *dependency* (03-doc:156). SMS/email precedent: `UsageMeteringService.record` from `twilio_webhooks.py:190` /
  `email_node_executor.py:150`. Building it now = a half-feature with no rollups (M-2)/cost/re-tagging (M-4) to consume it.
  Register: V-5 = Plan 11 **M-1**. **Deferred.**
- **V-9 (per-clinic Retell workspace / BYO-SIP):** infra/product/provisioning, not a code edit. Today one global
  `settings.retell_api_secret` (`config.py:79`, used in `voice_node_executor.py`). Scope §3.5/§7.2 requires a Retell workspace
  per clinic/DSO, per-location encrypted credential storage (Secrets Manager), SIP trunk binding of each clinic's Twilio
  sub-account numbers, region handling — overlaps **Plan 10** provisioning (PR-3). Non-cap. **Deferred.**

## Decision inputs for the user
1. **XC-1b timeout semantics (product/compliance).** V-6 currently classifies Retell timeout/network as *transient → retry*
   → a lost-response timeout (Retell placed the call but we didn't get the 200) re-dials = possible double-contact. Retell has
   no idempotency key (A-4). Options: (a) treat timeout as **terminal/at-most-once** (never double-dial; a rare genuine-failure
   timeout won't retry) — safest per the "never double-contact" rule; (b) keep retryable (current); (c) split: explicit 5xx =
   retry, ambiguous timeout/network = terminal. **This is a policy call, not guessable.**
2. **V-8 scope:** API-first (recommended) vs full-stack now.

## Disposition
- **Build now:** V-8 **backend API** (profiles CRUD + attempts drill-down read) + F-3 disconnection_reason threading.
- **Route to user:** XC-1b timeout policy; V-8 API-first-vs-FE.
- **Defer (justified):** V-5 → Plan 11; V-9 → infra/Plan 10; `calls`→run linkage columns (no consumer needs them — skip).
- **FE (V-8 UI):** fast follow after the API contract is frozen (small–medium; mirrors LocationAdminPanel + CampaignDetail).
