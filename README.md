## NexHealth Voice Agent Backend

This repo is the starting point for a HIPAA-minded voice agent backend that integrates with the NexHealth Synchronizer API. It uses FastAPI for HTTP endpoints and a small client wrapper for NexHealth auth and requests.

### Goals
- Provide a secure backend for a voice agent that needs scheduling and patient data.
- Centralize NexHealth authentication and rate-limit handling.
- Keep PHI out of logs and enforce least-privilege access.

### Why FastAPI
FastAPI gives async I/O, typed request models, and clean dependency injection. It is a good fit for a voice agent backend that will call NexHealth and potentially third-party services in the same request flow.

### Boilerplate decision
We can proceed with a minimal in-house setup to stay focused and HIPAA-minded. If we later decide to adopt a template, the Benav Labs FastAPI boilerplate is a strong option to compare against our needs, especially if we want built-in auth, background jobs, and caching. See: https://github.com/benavlabs/FastAPI-boilerplate

### Running locally
1. Create `.env` at the project root (see `docs/ENVIRONMENT.md`).
2. Install deps and run:
   - `python -m venv .venv && source .venv/bin/activate`
   - `pip install -e ".[dev]"` (or just `pip install pyngrok`)
   - `python src/app/main.py` (or `uvicorn src.app.main:app --reload`)

### Testing from Public Internet (Retell)
1. Ensure `NGROK_AUTH_TOKEN` is set (optional, but recommended).
2. Run the helper script:  
   `python scripts/start_ngrok.py`
3. Use the generated URL as your Agent's Base URL.

### Endpoints

See [API Reference](docs/API_REFERENCE.md) for full details.

**Key Resources:**
- `GET /api/v1/nexhealth/locations` - Find practices and subdomains
- `GET /api/v1/nexhealth/patients` - Lookup patients
- `GET /api/v1/nexhealth/appointment_slots` - Find booking availability
- `GET /api/v1/nexhealth/appointments` - Manage bookings
- `GET /api/v1/nexhealth/providers` - List doctors/providers
- `GET /api/v1/nexhealth/appointment_types` - List visit types

*Note: `institutions`, `availabilities`, and `operatories` endpoints are disabled for this Voice Agent implementation.*

### HIPAA-minded defaults
These are design notes, not legal advice.
- Do not log PHI. Avoid request/response body logging.
- Store API keys and tokens in env vars and in memory only.
- Use TLS everywhere and restrict inbound access.
- Ensure audit logging exists for access to patient data.
- Enforce role-based access to backend endpoints.
- Use encryption at rest for any persistent data.

For more detail see `docs/PROJECT.md` and `docs/NEXHEALTH.md`.
