SHELL := /bin/bash
COMPOSE := docker compose -f docker-compose.dev.yml

# =============================================================================
# BotNexHealth - Development Commands
# =============================================================================

.PHONY: help setup dev up up-deps up-app down logs api-logs web-logs db-logs redis-logs migrate test lint clean build run cdk-synth-staging cdk-deploy-staging cdk-run-migrations-staging cdk-publish-frontend-staging health

help:
	@echo "BotNexHealth Development Commands"
	@echo "================================="
	@echo ""
	@echo "Development:"
	@echo "  make setup     - Copy .env from .env.example if missing"
	@echo "  make dev       - Start local Docker stack, run migrations, tail logs"
	@echo "  make up        - Start local Docker stack without tailing logs"
	@echo "  make down      - Stop local Docker stack"
	@echo "  make logs      - Tail all Docker logs"
	@echo "  make migrate   - Run Alembic migrations inside the API container"
	@echo "  make test      - Run tests"
	@echo "  make lint      - Run linter (ruff)"
	@echo "  make clean     - Remove cache and build files"
	@echo ""
	@echo "Docker (local testing):"
	@echo "  make build     - Build Docker image"
	@echo "  make run       - Run Docker container locally"
	@echo ""
	@echo "CDK / ECS:"
	@echo "  make cdk-synth-staging            - Synthesize the staging CDK stack"
	@echo "  make cdk-deploy-staging           - Deploy the staging CDK stack"
	@echo "  make cdk-run-migrations-staging   - Run the ECS one-off migration task"
	@echo "  make cdk-publish-frontend-staging - Build and publish the frontend"
	@echo ""

# =============================================================================
# Development
# =============================================================================

setup:
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example"; fi

migrate: setup up-deps
	$(COMPOSE) run --rm api alembic upgrade head

dev: migrate up-app logs

up: migrate up-app

up-deps:
	$(COMPOSE) up --build -d postgres redis

up-app:
	$(COMPOSE) up --build -d api web

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

api-logs:
	$(COMPOSE) logs -f api

web-logs:
	$(COMPOSE) logs -f web

db-logs:
	$(COMPOSE) logs -f postgres

redis-logs:
	$(COMPOSE) logs -f redis

test:
	source .venv/bin/activate && pytest -v

lint:
	source .venv/bin/activate && ruff check src/

# =============================================================================
# Docker (for local testing only - cloud deployments use AWS)
# =============================================================================

build:
	docker build -t botnexhealth-api:latest .

run:
	docker run --rm -p 8000:8000 --env-file .env botnexhealth-api:latest

# =============================================================================
# CDK / ECS
# =============================================================================

cdk-synth-staging:
	cd infra && cdk synth -c config=config/staging.json

cdk-deploy-staging:
	cd infra && cdk deploy -c config=config/staging.json

cdk-run-migrations-staging:
	AWS_PROFILE=$${AWS_PROFILE:-deployer} CDK_STACK_NAME=$${CDK_STACK_NAME:-nex-health-staging} bash scripts/run_ecs_migration_task.sh

cdk-publish-frontend-staging:
	AWS_PROFILE=$${AWS_PROFILE:-deployer} CDK_STACK_NAME=$${CDK_STACK_NAME:-nex-health-staging} bash scripts/publish_frontend_from_cdk.sh

# =============================================================================
# Maintenance
# =============================================================================

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/ .coverage htmlcov/

health:
	@curl -s http://localhost:8000/livez | python3 -m json.tool || echo "API not responding"
