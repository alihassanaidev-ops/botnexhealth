# NexHealth Voice Agent Platform

AI voice agent for dental/medical clinics. Retell answers the clinic's phone,
our backend gives it function calls into NexHealth (patient lookup, slot
search, booking), and clinic staff get a dashboard with call transcripts,
summaries, tags, a callback queue, and daily metrics. Multi-tenant
(clinic = institution, with N locations) and HIPAA-minded.

Start with **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the system
overview. The rest of the docs:

| Doc | What it covers |
|---|---|
| [docs/NEXHEALTH.md](docs/NEXHEALTH.md) | PMS integration — auth, rate limits, slot/booking flow, and the API's caveats and edge cases |
| [docs/SECURITY.md](docs/SECURITY.md) | Auth, MFA, tenant isolation (RLS), PHI encryption, retention, audit |
| [docs/compliance/](docs/compliance/README.md) | HIPAA/PHIPA/PIPEDA readiness pack: scope, data inventory, vendors, gap register, policy drafts |
| [docs/DEPLOYMENT_AND_HIPAA_GUIDE.md](docs/DEPLOYMENT_AND_HIPAA_GUIDE.md) | Deploy runbook (staging → production) and infra compliance |
| [docs/SCHEDULED_JOBS.md](docs/SCHEDULED_JOBS.md) | Recurring jobs catalog + local debugging harness |
| [infra/README.md](infra/README.md) | CDK stack: what's provisioned and why ECS/Fargate |

## Repository layout

```
src/app/            FastAPI backend
  api/routes/       HTTP endpoints (auth, portal, calls, dashboard, admin, …)
  retell/           Voice agent: function dispatch, webhooks, idempotency
  nexhealth/        NexHealth transport: auth, HTTP client, rate limiting
  pms/              PMS adapter abstraction (NexHealth implementation)
  models/           SQLAlchemy models (28 tables, RLS-scoped tenancy)
  services/         Business logic (post-call pipeline, sync, retention, …)
  tasks/            Celery tasks (webhooks, email, SMS, recordings)
  scripts/          Operational one-offs and scheduled-job entrypoints
alembic/            Migrations (incl. RLS policies, audit partitioning)
nexus-dashboard-web/  React dashboard (Vite + TS)
infra/              AWS CDK (ECS Fargate, RDS, ElastiCache, S3/CloudFront, WAF)
tests/              unit / integration (testcontainers) / rls tiers
```

## Local development

Backend (Python 3.11+, [uv](https://docs.astral.sh/uv/)):

```bash
uv sync
cp .env.example .env        # fill in at minimum: DATABASE_*, JWT_SECRET, ENCRYPTION_KEY
uvicorn src.app.main:app --reload
```

Postgres is expected on `localhost:5433` for local work; Redis is optional
locally (rate limiting and token caching fall back to in-process, Celery needs
it). Run migrations with `alembic upgrade head`, create an admin with
`python -m src.app.scripts.create_super_admin`.

Frontend:

```bash
cd nexus-dashboard-web
npm install
npm run dev                  # expects the API on :8000 (VITE_API_URL to override)
```

Tests and lint:

```bash
make test                    # unit tier; integration/rls tiers need Docker
make lint                    # ruff
```

## Deployment

AWS via CDK, staging first: `make cdk-deploy-staging`, then
`make cdk-run-migrations-staging` (one-off ECS task), then
`make cdk-publish-frontend-staging`. Full runbook including production setup
in [docs/DEPLOYMENT_AND_HIPAA_GUIDE.md](docs/DEPLOYMENT_AND_HIPAA_GUIDE.md).
