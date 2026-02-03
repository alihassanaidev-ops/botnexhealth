# BotNexHealth Deployment Guide

> A comprehensive guide for developers to deploy and maintain the NexHealth Voice Agent Backend.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Server Access](#server-access)
5. [Directory Structure](#directory-structure)
6. [Configuration Files](#configuration-files)
7. [Secrets Management](#secrets-management)
8. [Common Operations](#common-operations)
9. [Monitoring & Logs](#monitoring--logs)
10. [Troubleshooting](#troubleshooting)
11. [SSL Certificate Management](#ssl-certificate-management)
12. [Updating the Application](#updating-the-application)
13. [Rollback Procedure](#rollback-procedure)
14. [Security Notes](#security-notes)

---

## Project Overview

BotNexHealth is a FastAPI backend that serves as a bridge between:

- **Retell AI** - Voice agent platform that handles phone calls
- **NexHealth API** - Dental practice management system

When a patient calls, Retell's voice agent processes the conversation and calls our API to:
- Look up patient information
- Check appointment availability
- Book appointments
- Create new patient records

### Live URL

**Production API:** `https://api.nexusdental.ai`

---

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Patient       │     │   Retell AI     │     │   NexHealth     │
│   (Phone Call)  │────▶│   Voice Agent   │     │   API           │
└─────────────────┘     └────────┬────────┘     └────────▲────────┘
                                 │                       │
                                 │ Webhook               │ API Calls
                                 ▼                       │
                        ┌────────────────────────────────┴───────┐
                        │         api.nexusdental.ai             │
                        │  ┌──────────────────────────────────┐  │
                        │  │            NGINX                 │  │
                        │  │   (SSL, Rate Limiting, Proxy)    │  │
                        │  └──────────────┬───────────────────┘  │
                        │                 │                      │
                        │  ┌──────────────▼───────────────────┐  │
                        │  │         FastAPI Backend          │  │
                        │  │   (Gunicorn + Uvicorn Workers)   │  │
                        │  └──────────────┬───────────────────┘  │
                        │                 │                      │
                        │  ┌──────────────▼───────────────────┐  │
                        │  │            Redis                 │  │
                        │  │      (Token Caching)             │  │
                        │  └──────────────────────────────────┘  │
                        └────────────────────────────────────────┘
```

### Container Overview

| Container | Purpose | Port |
|-----------|---------|------|
| `botnexhealth-nginx` | Reverse proxy, SSL termination, rate limiting | 80, 443 |
| `botnexhealth-api` | FastAPI application (4 worker processes) | 8000 (internal) |
| `botnexhealth-redis` | Token caching for NexHealth API | 6379 (internal) |
| `botnexhealth-certbot` | Automatic SSL certificate renewal | - |

---

## Prerequisites

Before working with this project, ensure you have:

1. **SSH access** to the production server
2. **Docker** and **Docker Compose** installed (already on server)
3. Basic understanding of:
   - Linux command line
   - Docker containers
   - REST APIs

---

## Server Access

### SSH Connection

```
Server IP: 76.13.111.90
User: deploy
Project Path: /home/deploy/projects/botnexhealth
```

### Important Paths

| Path | Description |
|------|-------------|
| `/home/deploy/projects/botnexhealth` | Main project directory |
| `/home/deploy/projects/botnexhealth/secrets` | API keys and secrets |
| `/home/deploy/projects/botnexhealth/certbot` | SSL certificates |
| `/home/deploy/projects/botnexhealth/docker` | Docker configurations |

---

## Directory Structure

```
botnexhealth/
├── src/
│   └── app/
│       ├── main.py              # Application entry point
│       ├── config.py            # Configuration & settings
│       ├── dependencies.py      # Dependency injection
│       ├── api/
│       │   ├── routes/          # API endpoint definitions
│       │   ├── models.py        # Request/response models
│       │   └── helpers.py       # Utility functions
│       ├── nexhealth/           # NexHealth API client
│       │   ├── client.py        # Main API client
│       │   ├── auth.py          # Authentication handling
│       │   └── token_manager.py # Token caching
│       └── retell/              # Retell AI integration
│           ├── functions.py     # Webhook endpoint
│           ├── handlers.py      # Function implementations
│           └── security.py      # Signature verification
├── docker/
│   └── nginx/
│       └── nginx.conf           # Nginx configuration
├── secrets/                     # API keys (NEVER commit)
│   ├── nexhealth_api_key.txt
│   ├── retell_api_secret.txt
│   └── admin_api_key.txt
├── certbot/                     # SSL certificates
│   ├── conf/                    # Let's Encrypt data
│   └── www/                     # ACME challenge files
├── Dockerfile                   # Container build instructions
├── docker-compose.yml           # Base compose configuration
├── docker-compose.prod.yml      # Production overrides
├── docker-compose.ssl.yml       # SSL configuration
└── Makefile                     # Shortcut commands
```

---

## Configuration Files

### Docker Compose Files

The application uses multiple compose files that are combined:

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Base configuration (networks, volumes, basic settings) |
| `docker-compose.prod.yml` | Production settings (secrets, resource limits) |
| `docker-compose.ssl.yml` | SSL certificates and certbot auto-renewal |

**Important:** Always use all three files together in production:
```
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ssl.yml <command>
```

Or use the Makefile shortcuts (recommended).

### Nginx Configuration

Located at: `docker/nginx/nginx.conf`

Key features configured:
- SSL/TLS termination (HTTPS)
- Rate limiting (100 requests/second per IP)
- Security headers (XSS protection, clickjacking prevention)
- Reverse proxy to FastAPI
- ACME challenge endpoint for SSL renewal

---

## Secrets Management

### Secret Files

All sensitive credentials are stored in the `secrets/` directory:

| File | Contains | Used For |
|------|----------|----------|
| `nexhealth_api_key.txt` | NexHealth API key | Authenticating with NexHealth |
| `retell_api_secret.txt` | Retell API secret | Verifying Retell webhook signatures |
| `admin_api_key.txt` | Admin API key | Protecting admin endpoints |

### Current Admin API Key

```
xbSp93E3tUUt3aFk0l299iOtDT0IOdg9TrhLSYJlNrU=
```

**Store this securely.** You need it to access the NexHealth proxy endpoints.

### Updating Secrets

1. Edit the appropriate file in `secrets/`
2. Restart the API container to pick up changes

**Never commit secrets to Git.** The `secrets/` directory is in `.gitignore`.

---

## Common Operations

### Starting the Application

```
cd /home/deploy/projects/botnexhealth
make prod-ssl
```

This starts all containers: nginx, api, redis, certbot.

### Stopping the Application

```
make prod-ssl-down
```

### Restarting After Code Changes

```
make prod-build      # Rebuild the Docker image
make prod-ssl-down   # Stop current containers
make prod-ssl        # Start with new image
```

### Quick Health Check

Visit in browser or curl:
- `https://api.nexusdental.ai/livez` - Should return `{"status":"alive"}`
- `https://api.nexusdental.ai/health` - Nginx health check

### View Running Containers

```
docker ps
```

Expected output shows 4 containers all in "healthy" or "Up" state.

---

## Monitoring & Logs

### View All Logs

```
make prod-ssl-logs
```

Press `Ctrl+C` to stop following logs.

### View Specific Container Logs

```
docker logs botnexhealth-api        # API application logs
docker logs botnexhealth-nginx      # Nginx access/error logs
docker logs botnexhealth-redis      # Redis logs
docker logs botnexhealth-certbot    # SSL renewal logs
```

### View Recent Logs Only

```
docker logs --tail 100 botnexhealth-api      # Last 100 lines
docker logs --since 1h botnexhealth-api      # Last hour
```

### Check Container Health

```
docker ps
```

Look at the STATUS column:
- `healthy` = Container is working correctly
- `unhealthy` = Container has issues (check logs)
- `starting` = Container is still initializing

### Check Resource Usage

```
docker stats
```

Shows CPU, memory, and network usage for each container.

---

## Troubleshooting

### Container Won't Start

1. Check logs for the specific container:
   ```
   docker logs botnexhealth-api
   ```

2. Common issues:
   - **Missing secrets:** Ensure all files exist in `secrets/`
   - **Port conflict:** Another service using port 80 or 443
   - **Permission error:** Check file permissions on secrets

### API Returns 502 Bad Gateway

This means nginx can't reach the API container.

1. Check if API container is running:
   ```
   docker ps | grep api
   ```

2. Check API logs for errors:
   ```
   docker logs botnexhealth-api --tail 50
   ```

3. Restart the API:
   ```
   docker restart botnexhealth-api
   ```

### API Returns 401 Unauthorized

For NexHealth endpoints (`/api/v1/nexhealth/*`):
- Ensure you're sending the `X-Admin-API-Key` header
- Verify the key matches `secrets/admin_api_key.txt`

For Retell endpoints (`/api/v1/retell/functions`):
- Retell must send valid `x-retell-signature` header
- Check that `RETELL_API_SECRET` is correct

### SSL Certificate Issues

If you see certificate errors:

1. Check certificate expiry:
   ```
   echo | openssl s_client -connect api.nexusdental.ai:443 2>/dev/null | openssl x509 -noout -dates
   ```

2. Force certificate renewal:
   ```
   make ssl-renew
   ```

### Container Using Too Much Memory

1. Check current usage:
   ```
   docker stats --no-stream
   ```

2. Restart the container:
   ```
   docker restart botnexhealth-api
   ```

### Redis Connection Issues

If you see Redis-related errors in API logs:

1. Check Redis is running:
   ```
   docker ps | grep redis
   ```

2. Restart Redis:
   ```
   docker restart botnexhealth-redis
   ```

---

## SSL Certificate Management

### Certificate Location

Certificates are stored in `certbot/conf/live/api.nexusdental.ai/`

### Automatic Renewal

The `certbot` container automatically checks for renewal every 12 hours. Certificates are renewed when they have less than 30 days remaining.

### Manual Renewal

If needed, force a renewal:

```
make ssl-renew
```

### Certificate Expiry

Current certificate expires: **April 30, 2026**

Let's Encrypt certificates are valid for 90 days, but auto-renewal handles this.

---

## Updating the Application

### Standard Update Process

1. **Pull latest code:**
   ```
   cd /home/deploy/projects/botnexhealth
   git pull origin main
   ```

2. **Rebuild the Docker image:**
   ```
   make prod-build
   ```

3. **Restart with new image:**
   ```
   make prod-ssl-down
   make prod-ssl
   ```

4. **Verify it's working:**
   ```
   curl https://api.nexusdental.ai/livez
   ```

### Zero-Downtime Update (Advanced)

For minimal disruption:

1. Build new image without stopping current:
   ```
   docker compose -f docker-compose.yml build api
   ```

2. Recreate only the API container:
   ```
   docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ssl.yml up -d --no-deps api
   ```

This replaces the API container while nginx continues serving (brief ~5 second gap).

---

## Rollback Procedure

If an update causes issues:

### Quick Rollback (if image still exists)

1. Find previous image:
   ```
   docker images | grep botnexhealth
   ```

2. Tag old image and redeploy:
   ```
   docker tag <old-image-id> botnexhealth-api:rollback
   ```

### Full Rollback (using Git)

1. **Stop current deployment:**
   ```
   make prod-ssl-down
   ```

2. **Revert to previous commit:**
   ```
   git log --oneline -5          # Find the commit to revert to
   git checkout <commit-hash>    # Switch to that commit
   ```

3. **Rebuild and deploy:**
   ```
   make prod-build
   make prod-ssl
   ```

4. **Verify:**
   ```
   curl https://api.nexusdental.ai/livez
   ```

---

## Security Notes

### What's Protected

| Layer | Protection |
|-------|------------|
| Network | Only ports 80 and 443 exposed |
| SSL/TLS | TLS 1.2+ only, strong ciphers |
| Rate Limiting | 100 req/sec per IP |
| Headers | XSS, clickjacking, MIME sniffing protection |
| Containers | Non-root user, read-only filesystem |
| Secrets | Mounted as files, not environment variables |

### Authentication Summary

| Endpoint Type | Auth Method |
|--------------|-------------|
| Health checks (`/livez`, `/readyz`) | None (public) |
| Admin health (`/api/v1/health`) | `X-Admin-API-Key` header |
| NexHealth proxy (`/api/v1/nexhealth/*`) | `X-Admin-API-Key` header |
| Retell webhooks (`/api/v1/retell/*`) | Retell signature verification |

### HIPAA Considerations

- Patient data (PHI) is **never logged**
- Only hashed call IDs appear in logs
- No data is stored locally - all data flows through to NexHealth

### If Credentials Are Compromised

1. **Immediately rotate the compromised secret:**
   - Edit the appropriate file in `secrets/`
   - Restart: `docker restart botnexhealth-api`

2. **For NexHealth API key:**
   - Generate new key in NexHealth dashboard
   - Update `secrets/nexhealth_api_key.txt`

3. **For Retell secret:**
   - Generate new secret in Retell dashboard
   - Update `secrets/retell_api_secret.txt`

4. **For Admin API key:**
   - Generate new random key
   - Update `secrets/admin_api_key.txt`
   - Update any services using the old key

---

## Quick Reference

### Essential Commands

| Action | Command |
|--------|---------|
| Start production | `make prod-ssl` |
| Stop production | `make prod-ssl-down` |
| View logs | `make prod-ssl-logs` |
| Rebuild image | `make prod-build` |
| Check status | `docker ps` |
| Health check | `curl https://api.nexusdental.ai/livez` |

### Important URLs

| URL | Purpose |
|-----|---------|
| `https://api.nexusdental.ai/livez` | Public health check |
| `https://api.nexusdental.ai/api/v1/health` | Authenticated health check |
| `https://api.nexusdental.ai/api/v1/nexhealth/*` | NexHealth proxy endpoints |
| `https://api.nexusdental.ai/api/v1/retell/functions` | Retell webhook endpoint |

### Emergency Contacts

If you encounter issues you can't resolve:
1. Check the logs first
2. Review this troubleshooting guide
3. Contact the senior developer or DevOps team

---

## Appendix: API Endpoints

### Public Endpoints (No Auth)

- `GET /livez` - Liveness probe
- `GET /readyz` - Readiness probe

### Admin Endpoints (Requires X-Admin-API-Key)

- `GET /api/v1/health` - Detailed health check
- `GET /api/v1/nexhealth/locations` - List locations
- `GET /api/v1/nexhealth/patients` - Search patients
- `POST /api/v1/nexhealth/patients` - Create patient
- `GET /api/v1/nexhealth/providers` - List providers
- `GET /api/v1/nexhealth/appointments` - List appointments
- `POST /api/v1/nexhealth/appointments` - Book appointment
- `GET /api/v1/nexhealth/appointment_slots` - Get available slots
- `GET /api/v1/nexhealth/appointment_types` - List appointment types

### Retell Endpoints (Requires x-retell-signature)

- `POST /api/v1/retell/functions?name=<function>` - Execute function

Available functions:
- `lookup_patient`
- `create_patient`
- `check_availability`
- `book_appointment`

---

*Last Updated: January 2026*
