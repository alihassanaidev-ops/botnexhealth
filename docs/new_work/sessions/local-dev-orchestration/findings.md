# Findings & Decisions

## Requirements
- Add a full local Docker Compose development stack.
- Let developers run the project with a simple `make dev` flow.
- Keep this local-only and avoid production-sensitive changes.
- Do not add a worker service.
- Do not add `.env.local.example`; keep `.env.example` as the canonical env reference.
- Document this work using the planning-with-files three-file session structure.
- Let local SUPER_ADMIN users use authenticator-app TOTP when passkeys/fingerprint are unavailable.

## Research Findings
- The original `make dev` only started the backend API locally with `uvicorn`.
- The repository did not have a Docker Compose file.
- The backend already has a production Dockerfile that can run the API.
- The frontend has no Dockerfile, but can run in a standard Node container.
- The frontend Vite config uses port `3000`, not `5173`.
- The app reads `.env` through Pydantic settings.
- `docker compose config` fails if `env_file: .env` is required and `.env` does not exist.
- SUPER_ADMIN is passkey-only by default.
- TOTP works on localhost because it is QR/secret based; passkeys also work on localhost but need an OS passkey provider, PIN, fingerprint, or hardware security key.
- The local clinic dashboard data comes from `src.app.scripts.seed_demo_data`, not production.
- `uv run pytest` did not work directly because `pytest` is not available from the declared project dependencies/dev extra; the focused run used transient `--with pytest --with pytest-asyncio`.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Add `docker-compose.dev.yml` | Keeps local orchestration separate from production Docker/CDK deployment. |
| Use services: `postgres`, `redis`, `api`, `web` | Provides the full local app without introducing unnecessary worker infrastructure. |
| Make `.env` optional in Compose | Allows config validation and first-run setup before `.env` exists. |
| Override container DB/Redis URLs in Compose | Containers must use service hostnames like `postgres` and `redis`, not `localhost`. |
| Run frontend with `node:22-bookworm-slim` | Avoids adding a frontend Dockerfile for local dev only. |
| Keep `make test` and `make lint` local | Existing test/lint flow depends on local Python tooling and was not part of this startup problem. |
| Add `DEV_ALLOW_SUPER_ADMIN_TOTP` local escape hatch | Lets local super admins use authenticator apps while keeping production passkey-only through an `APP_ENV` guard. |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| `.env.local.example` felt unnecessary and duplicated `.env.example` | Removed it and documented local overrides in README / Compose instead. |
| Full-stack Compose initially considered a worker | Excluded it after user clarification. |
| Compose validation needed `.env` | Made `.env` optional and supplied safe local fallback secrets. |
| Super-admin local login could not complete without fingerprint/passkey setup | Added non-production-only TOTP allowance and enabled it in `docker-compose.dev.yml`. |

## Resources
- `Makefile`
- `README.md`
- `docker-compose.dev.yml`
- `Dockerfile`
- `nexus-dashboard-web/vite.config.ts`
- `src/app/config.py`
- `src/app/services/mfa.py`
- `src/app/api/routes/auth.py`
- `tests/unit/test_mfa_services.py`
- `.claude/CLAUDE.md` planning-with-files section

## Visual/Browser Findings
- No browser or visual inspection was used.
