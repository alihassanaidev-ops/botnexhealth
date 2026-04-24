# =============================================================================
# BotNexHealth - Development Commands
# =============================================================================

.PHONY: help dev test lint clean build run cdk-synth-staging cdk-deploy-staging cdk-run-migrations-staging cdk-publish-frontend-staging health

help:
	@echo "BotNexHealth Development Commands"
	@echo "================================="
	@echo ""
	@echo "Development:"
	@echo "  make dev       - Start development server with hot reload"
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

dev:
	source .venv/bin/activate && uvicorn src.app.main:app --host 0.0.0.0 --port 8000 --reload

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
