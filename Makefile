# =============================================================================
# BotNexHealth - Docker Management Commands
# =============================================================================

.PHONY: help build up down logs shell test clean prod prod-up prod-down secrets-init ssl-init prod-ssl

# Default target
help:
	@echo "BotNexHealth Docker Commands"
	@echo "============================"
	@echo ""
	@echo "Development:"
	@echo "  make build     - Build Docker images"
	@echo "  make up        - Start development stack (API only, hot reload)"
	@echo "  make up-nginx  - Start with nginx reverse proxy"
	@echo "  make down      - Stop all containers"
	@echo "  make logs      - Follow API logs"
	@echo "  make shell     - Open shell in API container"
	@echo "  make test      - Run tests in container"
	@echo ""
	@echo "Production:"
	@echo "  make secrets-init  - Create secrets directory (run first!)"
	@echo "  make ssl-init      - Get SSL certificate from Let's Encrypt"
	@echo "  make prod-build    - Build production image"
	@echo "  make prod-ssl      - Start production with SSL (recommended)"
	@echo "  make prod-up       - Start production stack (no SSL)"
	@echo "  make prod-down     - Stop production stack"
	@echo "  make prod-logs     - Follow production logs"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean     - Remove containers, volumes, and images"
	@echo "  make prune     - Docker system prune"
	@echo ""

# =============================================================================
# Development Commands
# =============================================================================

build:
	docker compose build

up:
	docker compose up -d api
	@echo "API running at http://localhost:8000"
	@echo "Health check: curl -H 'X-Admin-API-Key: your-key' http://localhost:8000/api/v1/health"

up-nginx:
	docker compose --profile with-nginx up -d
	@echo "Stack running at http://localhost"

down:
	docker compose down

logs:
	docker compose logs -f api

logs-all:
	docker compose logs -f

shell:
	docker compose exec api /bin/bash

test:
	docker compose exec api pytest -v

restart:
	docker compose restart api

# =============================================================================
# Production Commands
# =============================================================================

prod-build:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml build

prod-up:
	@if [ ! -f .env ]; then \
		echo "ERROR: .env file not found. Copy .env.example to .env and configure it."; \
		exit 1; \
	fi
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
	@echo "Production stack starting..."
	@echo "Check status: make prod-status"

prod-down:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml down

prod-logs:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f

prod-status:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml ps

prod-restart:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml restart

prod-scale:
	@echo "Scaling API to $(n) replicas..."
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --scale api=$(n)

# =============================================================================
# SSL Commands
# =============================================================================

ssl-init:
	@echo "Initializing SSL certificate for api.nexusdental.ai..."
	./scripts/init-ssl.sh

prod-ssl:
	@if [ ! -f .env ] && [ ! -d secrets ]; then \
		echo "ERROR: No .env or secrets/ found. Run 'make secrets-init' first."; \
		exit 1; \
	fi
	docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ssl.yml up -d
	@echo ""
	@echo "Production stack with SSL starting..."
	@echo "API available at: https://api.nexusdental.ai/api/v1/"
	@echo ""
	@echo "Check status: make prod-status"

prod-ssl-down:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ssl.yml down

prod-ssl-logs:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ssl.yml logs -f

ssl-renew:
	@echo "Forcing SSL certificate renewal..."
	docker compose -f docker-compose.yml -f docker-compose.ssl.yml run --rm certbot-init \
		renew --force-renewal
	docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ssl.yml exec nginx nginx -s reload
	@echo "Certificate renewed and nginx reloaded"

# =============================================================================
# Secrets Management (Production)
# =============================================================================

secrets-init:
	@echo "Creating secrets directory..."
	@mkdir -p secrets
	@chmod 700 secrets
	@if [ ! -f secrets/nexhealth_api_key.txt ]; then \
		echo "Creating nexhealth_api_key.txt (edit with your key)"; \
		echo "your-nexhealth-api-key-here" > secrets/nexhealth_api_key.txt; \
	fi
	@if [ ! -f secrets/retell_api_secret.txt ]; then \
		echo "Creating retell_api_secret.txt (edit with your key)"; \
		echo "your-retell-api-secret-here" > secrets/retell_api_secret.txt; \
	fi
	@if [ ! -f secrets/admin_api_key.txt ]; then \
		echo "Creating admin_api_key.txt with random key"; \
		openssl rand -base64 32 > secrets/admin_api_key.txt; \
	fi
	@chmod 600 secrets/*.txt
	@echo ""
	@echo "Secrets directory created at ./secrets/"
	@echo "IMPORTANT: Edit the secret files with your actual API keys before deploying!"
	@echo ""
	@echo "Files to edit:"
	@echo "  - secrets/nexhealth_api_key.txt"
	@echo "  - secrets/retell_api_secret.txt"
	@echo "  - secrets/admin_api_key.txt (auto-generated, but you can change it)"

secrets-show:
	@echo "Current secrets (masked):"
	@echo "  nexhealth_api_key: $$(head -c 10 secrets/nexhealth_api_key.txt 2>/dev/null || echo 'NOT SET')..."
	@echo "  retell_api_secret: $$(head -c 10 secrets/retell_api_secret.txt 2>/dev/null || echo 'NOT SET')..."
	@echo "  admin_api_key:     $$(head -c 10 secrets/admin_api_key.txt 2>/dev/null || echo 'NOT SET')..."

# =============================================================================
# Maintenance Commands
# =============================================================================

clean:
	docker compose down -v --rmi local
	docker compose -f docker-compose.yml -f docker-compose.prod.yml down -v --rmi local

prune:
	docker system prune -af --volumes

# Health check
health:
	@curl -s http://localhost:8000/livez | python3 -m json.tool || echo "API not responding"

health-full:
	@curl -s -H "X-Admin-API-Key: $${ADMIN_API_KEY}" http://localhost:8000/api/v1/health | python3 -m json.tool || echo "API not responding or invalid API key"

# View container resource usage
stats:
	docker stats --no-stream
