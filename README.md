# BotNexHealth

HIPAA-minded voice agent backend integrating **NexHealth** (scheduling/patient data) and **Retell AI** (voice agent) APIs.

## Features

- **NexHealth Integration**: Patient lookup, appointment scheduling, provider management
- **Retell AI Integration**: Voice agent function handlers and webhooks
- **Docker Deployment**: Production-ready with nginx reverse proxy and SSL
- **HIPAA-minded Design**: No PHI logging, secrets management, TLS everywhere

## Architecture

```
src/app/
├── api/
│   ├── routes/          # NexHealth REST endpoints
│   ├── models.py        # Request/response models
│   └── helpers.py       # API utilities
├── nexhealth/           # NexHealth client and auth
├── retell/
│   ├── handlers.py      # Voice agent function handlers
│   ├── webhooks.py      # Retell webhook endpoints
│   └── functions.py     # Function registry
├── config.py            # Settings and secrets management
└── main.py              # FastAPI application
```

## Quick Start

### Local Development

```bash
# 1. Clone and setup
git clone https://github.com/alihassanaidev-ops/botnexhealth.git
cd botnexhealth

# 2. Create virtual environment
python -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -e ".[dev]"

# 4. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 5. Run
uvicorn src.app.main:app --reload
```

### Docker Deployment

```bash
# Development
make dev

# Production with SSL
make prod
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for full deployment guide.

## Configuration

Create a `.env` file with:

```env
NEXHEALTH_API_KEY=your-nexhealth-api-key
RETELL_API_SECRET=your-retell-api-secret
ADMIN_API_KEY=your-admin-api-key
APP_ENV=production
LOG_LEVEL=info
```

| Variable | Required | Description |
|----------|----------|-------------|
| `NEXHEALTH_API_KEY` | Yes | NexHealth API key |
| `RETELL_API_SECRET` | Yes | Retell webhook verification |
| `ADMIN_API_KEY` | Yes | Admin endpoint authentication |
| `APP_ENV` | No | Environment (local/production) |
| `LOG_LEVEL` | No | Logging level (debug/info/warning/error) |

## Retell Voice Agent Functions

The following functions are available for Retell AI voice agents:

| Function | Description |
|----------|-------------|
| `lookup_patient` | Search patients by name, email, phone, or DOB |
| `create_patient` | Register new patient |
| `find_appointment_slots` | Find available booking slots |
| `book_appointment` | Book an appointment |
| `cancel_appointment` | Cancel existing appointment |
| `reschedule_appointment` | Cancel and rebook appointment |
| `list_locations` | List practice locations |
| `get_location_details` | Get location hours, address, etc. |
| `list_providers` | List providers with appointment types |
| `list_operatories` | List operatories/rooms |

## API Endpoints

### Health Checks
- `GET /livez` - Liveness probe (no auth)
- `GET /readyz` - Readiness probe (no auth)
- `GET /health` - Detailed health status

### NexHealth API (requires `X-API-Key`)
- `GET /api/v1/nexhealth/locations` - List locations
- `GET /api/v1/nexhealth/patients` - Search patients
- `GET /api/v1/nexhealth/providers` - List providers
- `GET /api/v1/nexhealth/appointment_slots` - Find availability
- `POST /api/v1/nexhealth/appointments` - Book appointment

### Retell Webhooks
- `POST /api/v1/retell/webhook` - Voice agent function calls

## HIPAA Considerations

> These are design notes, not legal advice.

- PHI is not logged (request/response body logging disabled)
- API keys stored in environment variables or Docker secrets
- TLS enforced in production
- Admin endpoints require authentication
- Audit logging for patient data access

## Project Structure

```
botnexhealth/
├── src/app/              # Application code
├── docker/               # Docker configs (nginx)
├── scripts/              # Deployment scripts
├── docs/                 # Documentation
├── docker-compose.yml    # Base compose file
├── docker-compose.prod.yml   # Production overrides
├── Dockerfile            # Multi-stage build
└── Makefile              # Common commands
```

## Author

Zulkaif <zulkaifahmed97@gmail.com>
