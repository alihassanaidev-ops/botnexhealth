# =============================================================================
# BotNexHealth - Development Commands
# =============================================================================
# For production, deploy to Render (see render.yaml)
# =============================================================================

.PHONY: help dev test lint clean

# Default target
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
	@echo "Production:"
	@echo "  Deploy via Render - push to GitHub, Render auto-deploys"
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
# Docker (for local testing only - production uses Render)
# =============================================================================

build:
	docker build -t botnexhealth-api:latest .

run:
	docker run --rm -p 8000:8000 --env-file .env botnexhealth-api:latest

# =============================================================================
# Maintenance
# =============================================================================

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/ .coverage htmlcov/

# Health check
health:
	@curl -s http://localhost:8000/livez | python3 -m json.tool || echo "API not responding"
