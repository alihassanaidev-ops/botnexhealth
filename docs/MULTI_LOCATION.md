# Multi-Location Tenant Architecture

**Status**: Implemented on `feature/universal-pms-adapter`
**Concept**: Tenant = institution/dental group, TenantLocation = physical practice

---

## Data Model

```
Tenant (institution)
├── nexhealth_api_key_encrypted    ← shared institution-level PMS credentials
├── sikka_app_id/secret_encrypted  ← shared institution-level PMS credentials
├── nexhealth_subdomain            ← default subdomain (kept for backward compat)
├── nexhealth_location_id          ← default location (kept for backward compat)
├── retell_agent_id                ← default agent (kept for backward compat)
│
├── TenantLocation (practice A)
│   ├── nexhealth_subdomain        ← overrides tenant default
│   ├── nexhealth_location_id      ← overrides tenant default
│   ├── retell_agent_id            ← location-specific voice agent
│   ├── retell_api_secret_encrypted
│   ├── address, city, state, phone, timezone
│   │
│   ├── TenantProvider[]           ← cached from PMS via sync
│   └── TenantAppointmentType[]    ← cached from PMS via sync
│
└── TenantLocation (practice B)
    └── ...
```

### Key Design Decisions

1. **Backward compatible** — All existing `Tenant` location fields are kept. Old code that reads `tenant.nexhealth_location_id` still works.
2. **Credential inheritance** — API keys live on Tenant (shared across locations). Subdomain/location_id/agent_id can be overridden per location.
3. **Slug-based routing** — Both tenants and locations use slugs. Locations are globally unique (enforced by DB constraint).
4. **Soft delete** — `is_active=False` for locations, same pattern as tenants.

---

## Resolution Order (Fallback Chain)

### NexHealth Adapter

```
location.nexhealth_subdomain  →  tenant.nexhealth_subdomain  →  global settings
location.nexhealth_location_id → tenant.nexhealth_location_id → global settings
```

### Retell Agent Lookup

```
1. TenantLocation.retell_agent_id == agent_id  →  (tenant, location)
2. Tenant.retell_agent_id == agent_id          →  (tenant, None)   ← backward compat
```

### PMS Adapter Cache

```
With location:    cache key = "{tenant_id}:{location_id}"
Without location: cache key = "{tenant_id}"
```

---

## Request Flow

### API Requests (via middleware)

```
Client sends:
  X-Tenant-Slug: acme-dental
  X-Location-Slug: acme-main        ← optional

Middleware resolves:
  request.state.tenant   = Tenant
  request.state.location = TenantLocation | None

get_tenant_pms() dependency:
  if location → get_adapter_for_tenant_location(tenant, location)
  else        → get_adapter_for_tenant(tenant)
```

### Retell Webhooks (via handler)

```
Retell sends agent_id in payload

get_tenant_from_call_context():
  1. Query TenantLocation where retell_agent_id = agent_id
  2. Fallback: Query Tenant where retell_agent_id = agent_id
  Returns (Tenant, TenantLocation | None)

_get_adapter():
  if location → get_adapter_for_tenant_location(tenant, location)
  else        → get_adapter_for_tenant(tenant)
```

---

## Sync Service

Caches provider and appointment type data from PMS into local tables. No PHI stored.

```python
from src.app.services.sync_service import SyncService

async with get_db_session() as session:
    sync = SyncService(session)
    result = await sync.sync_location(tenant, location)
    # result.providers_synced, result.appointment_types_synced, result.errors
```

### What Gets Cached

| Table | Fields | Source |
|-------|--------|--------|
| `tenant_providers` | name, first_name, last_name, specialty, source_id | PMS `list_providers()` |
| `tenant_appointment_types` | name, duration_minutes, source_metadata, source_id | PMS `list_appointment_types()` |

Upsert key: `(tenant_id, location_id, source_id)`

### Trigger Sync

- Admin API: `POST /admin/tenants/{slug}/locations/{loc_slug}/sync`
- Migration script: `python -m src.app.scripts.migrate_tenant_locations --sync`

---

## Migration Script

For existing tenants that already have `nexhealth_location_id` or `retell_agent_id` set directly on the Tenant model:

```bash
# Dry run (creates TenantLocation rows, no PMS sync)
python -m src.app.scripts.migrate_tenant_locations

# With PMS sync (also populates cached providers + appointment types)
python -m src.app.scripts.migrate_tenant_locations --sync
```

What it does:
1. Finds active tenants with location data on the Tenant model
2. Skips tenants that already have TenantLocation rows
3. Creates a `{tenant-name}-main` location copying the tenant's fields
4. Optionally syncs PMS data

---

## Files Overview

| File | Role |
|------|------|
| `src/app/models/tenant_location.py` | TenantLocation SQLAlchemy model |
| `src/app/models/tenant_provider.py` | TenantProvider (cached PMS data) |
| `src/app/models/tenant_appointment_type.py` | TenantAppointmentType (cached PMS data) |
| `src/app/services/sync_service.py` | SyncService — fetches + caches PMS data |
| `src/app/services/tenant_service.py` | Location CRUD methods on TenantService |
| `src/app/pms/factory.py` | `get_adapter_for_tenant_location()` + location cache |
| `src/app/pms/nexhealth/adapter.py` | `NexHealthAdapter.create(tenant, location=)` |
| `src/app/retell/functions.py` | `get_tenant_from_call_context()` returns tuple |
| `src/app/retell/handlers.py` | `_get_adapter()` uses location when available |
| `src/app/api/routes/tenants.py` | Location admin endpoints |
| `src/app/middleware/tenant.py` | `X-Location-Slug` header support |
| `src/app/scripts/migrate_tenant_locations.py` | One-time data migration |

---

## Future Work / TODOs

- [ ] **Alembic migration** — Create proper DB migration instead of relying on `create_tables()`
- [ ] **Remove deprecated Tenant fields** — Once all tenants are migrated, remove `nexhealth_location_id`, `retell_agent_id`, `retell_api_secret_encrypted` from Tenant model
- [ ] **Scheduled sync** — Run sync on a cron (e.g., nightly) to keep cached data fresh
- [ ] **Location-scoped Sikka adapter** — Currently Sikka adapter doesn't use location overrides (NexHealth does)
- [ ] **Dashboard UI** — Location management page in nexus-dashboard-web
- [ ] **Location-scoped audit logs** — Add `location_id` to AuditLog model for per-location filtering
- [ ] **Multi-location Retell** — Different voice prompts/personas per location
- [ ] **Location-level GHL integration** — GoHighLevel location_id per TenantLocation
- [ ] **Provider-to-location auto-mapping** — During sync, detect which providers work at which locations
- [ ] **Tests** — Unit tests for SyncService, TenantService location methods, middleware location resolution
