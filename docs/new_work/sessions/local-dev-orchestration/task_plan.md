# Task Plan: Local Dev Orchestration

## Goal
Make local development startable with one clear Docker Compose-based workflow for the API, frontend, Postgres, and Redis, without changing production deployment behavior.

## Current Phase
Phase 5

## Phases

### Phase 1: Requirements & Discovery
- [x] Confirm current `make dev` behavior
- [x] Confirm whether a Docker Compose stack already exists
- [x] Inspect backend Dockerfile and frontend dev server config
- [x] Document findings in `findings.md`
- **Status:** complete

### Phase 2: Local Dependency Compose
- [x] Add local Docker Compose services for Postgres and Redis
- [x] Preserve existing production Dockerfile behavior
- **Status:** complete

### Phase 3: Full Local Stack
- [x] Add API service to local Compose
- [x] Add frontend web service to local Compose
- [x] Avoid adding a Celery worker service because it is not needed for this project flow
- [x] Run migrations through the API container
- **Status:** complete

### Phase 4: Makefile & Docs
- [x] Update Makefile commands to support CTO-style one-command local startup
- [x] Update README local development instructions
- [x] Keep local orchestration scoped away from CDK/staging/production
- **Status:** complete

### Phase 5: Verification & Commit Prep
- [x] Validate Docker Compose config
- [x] Dry-run `make dev`
- [x] Add local-dev-only super-admin TOTP support for machines without passkeys
- [x] Review changed files
- [ ] Commit changes
- **Status:** in_progress

## Key Questions
1. Should this be a dependencies-only Compose stack or a full local app stack?
   - Answer: Full local stack, because the team wants CTO-style `make dev` ergonomics.
2. Should the local stack include a Celery worker?
   - Answer: No. The user clarified a worker is not needed.
3. Should `.env.local.example` exist?
   - Answer: No. The user preferred keeping `.env.example` as the canonical env template.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Add root `sessions/local-dev-orchestration/` | User explicitly asked for a root `sessions` folder for this work. |
| Use full local Docker Compose | Reduces startup from multiple terminals to `make dev`. |
| Keep `.env.example` canonical | Avoids maintaining two env examples and preserves important vendor config visibility. |
| Do not add worker service | User clarified it is not needed for this project. |
| Run migrations inside API container | Matches the CTO-style workflow and removes local venv as a runtime requirement for startup. |
| Use frontend port `3000` | `nexus-dashboard-web/vite.config.ts` sets Vite server port to `3000`. |
| Allow SUPER_ADMIN TOTP only in non-production when explicitly enabled | Supports local developers without fingerprint/security-key setup while keeping production passkey-only. |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| `docker compose config` failed when `.env` was missing | 1 | Made `.env` optional in Compose and provided local fallback `JWT_SECRET` / `ENCRYPTION_KEY` values for config validation. |
| Initial full-stack patch included a worker service | 1 | User interrupted and clarified no worker should be added; final stack only includes `postgres`, `redis`, `api`, and `web`. |
| SUPER_ADMIN login required a passkey on a ThinkPad without fingerprint configured | 1 | Added `DEV_ALLOW_SUPER_ADMIN_TOTP=true` to local Compose and guarded it so production ignores the flag. |
| `uv run pytest ...` could not find `pytest` | 1 | Used `uv run --with pytest --with pytest-asyncio pytest ...` for the focused test run. |

## Notes
- Planning-with-files rule: one workstream folder keeps exactly three files: `task_plan.md`, `findings.md`, and `progress.md`.
- If a feature takes multiple days or PRs, do not create `session_1.md`, `session_2.md`, etc.
- Continue in the same three files and add sections in `progress.md`, for example:
  - `## Slice 1 - Schema`
  - `## Slice 2 - Service Skeleton`
  - `## Slice 3 - Timer Claiming`
