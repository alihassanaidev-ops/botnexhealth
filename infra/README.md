# CDK/ECS Deployment

This repo now has a first-class AWS CDK deployment target under [`infra/`](./).

The target architecture is:

- ECS Fargate service for the FastAPI API
- ECS Fargate service for the Celery worker
- Explicit one-off ECS task definition for Alembic migrations
- RDS PostgreSQL
- ElastiCache Redis with TLS enabled
- S3 bucket for recordings
- S3 + CloudFront for the frontend
- WAF attached to the API load balancer
- Secrets Manager for generated app secrets, plus imported external secrets

## Why move away from Copilot

Copilot is a good ECS abstraction, but it is no longer the right long-term control plane for this repo. CDK gives you:

- versioned infra code that matches the rest of the repo
- no dependency on Copilot's lifecycle
- explicit resource ownership and outputs
- a clean path to CI/CD later without reverse-engineering generated stacks

## Config files

Use a JSON config per environment:

- [`config/staging.json`](./config/staging.json)
- [`config/production.example.json`](./config/production.example.json)

Run CDK with a specific config file:

```bash
cd infra
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cdk synth -c config=config/staging.json
cdk deploy -c config=config/staging.json
```

## Required external secrets

The stack generates the database credentials secret, `JWT_SECRET`, and `ENCRYPTION_KEY`. It still expects these secrets to already exist in Secrets Manager:

- `NEXHEALTH_API_KEY`
- `RETELL_API_SECRET`
- `RESEND_API_KEY`
- `RESEND_FROM_EMAIL`
- `TWILLIO_SID`
- `TWILLIO_API_SECRET`

Optional:

- `RESEND_REPLY_TO`
- `RESEND_ALERT_RECIPIENTS`

The stack injects database host, port, name, username, and password directly into ECS, so you do not need a separate `DATABASE_URL` secret.

The stack also derives the non-secret `PUBLIC_API_URL` deployment setting from
`api.domainName`. The application uses it to configure Twilio inbound SMS
webhooks and outbound delivery callbacks; it does not belong in Secrets Manager.

`trustedProxyCidrs` is also passed to Gunicorn/Uvicorn as the trusted forwarded
proxy set. This is required for signature-verified webhooks behind the ALB:
Twilio signs the public HTTPS URL, and the application must honor the ALB's
`X-Forwarded-Proto` value when reconstructing that URL.

## Deploy flow

1. `cdk deploy` the stack for the target environment.
2. Populate or update the external secrets if needed.
3. Run the migration task with [`scripts/run_ecs_migration_task.sh`](../scripts/run_ecs_migration_task.sh).
4. Publish the frontend with [`scripts/publish_frontend_from_cdk.sh`](../scripts/publish_frontend_from_cdk.sh).

This keeps schema changes explicit instead of hiding them inside API container startup.
