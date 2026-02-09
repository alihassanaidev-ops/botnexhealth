# Location Management API

**Base URL**: `https://<api-domain>/admin/tenants/{slug}/locations`
**Authentication**: Requires `Authorization: Bearer <JWT_TOKEN>` (User must have `ADMIN` role).

---

## 1. Create Location

Create a new physical practice location under a tenant.

*   **Endpoint**: `POST /admin/tenants/{slug}/locations`

**Request Body:**
```json
{
  "name": "Acme Dental - Downtown",
  "slug": "acme-downtown",
  "nexhealth_subdomain": "silora-demo-practice",
  "nexhealth_location_id": "340582",
  "retell_agent_id": "agent_abc123",
  "retell_api_secret": "sk-retell-secret",
  "address": "123 Main St",
  "city": "San Francisco",
  "state": "CA",
  "phone": "+14155551234",
  "timezone": "America/Los_Angeles"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string | Yes | Display name (max 255) |
| `slug` | string | Yes | URL-safe identifier (lowercase, hyphens, max 100). Must be globally unique. |
| `nexhealth_subdomain` | string | No | Overrides tenant-level subdomain |
| `nexhealth_location_id` | string | No | NexHealth location ID for this practice |
| `retell_agent_id` | string | No | Retell agent ID for this location's voice agent |
| `retell_api_secret` | string | No | Retell API secret (stored encrypted) |
| `address` | string | No | Street address |
| `city` | string | No | City |
| `state` | string | No | State/province |
| `phone` | string | No | Phone number |
| `timezone` | string | No | IANA timezone (e.g., `America/New_York`) |

**Response (201 Created):**
```json
{
  "id": "location-uuid",
  "tenant_id": "tenant-uuid",
  "name": "Acme Dental - Downtown",
  "slug": "acme-downtown",
  "is_active": true,
  "nexhealth_subdomain": "silora-demo-practice",
  "nexhealth_location_id": "340582",
  "retell_agent_id": "agent_abc123",
  "has_retell_secret": true,
  "address": "123 Main St",
  "city": "San Francisco",
  "state": "CA",
  "phone": "+14155551234",
  "timezone": "America/Los_Angeles"
}
```

**Errors:**
- `404` — Tenant not found
- `409` — Location slug already exists

---

## 2. List Locations

List all locations for a tenant.

*   **Endpoint**: `GET /admin/tenants/{slug}/locations`
*   **Query Parameters**:
    *   `include_inactive` (boolean, default: `false`): Include soft-deleted locations.

**Response (200 OK):**
```json
[
  {
    "id": "location-uuid-1",
    "tenant_id": "tenant-uuid",
    "name": "Acme Dental - Downtown",
    "slug": "acme-downtown",
    "is_active": true,
    "nexhealth_subdomain": "silora-demo-practice",
    "nexhealth_location_id": "340582",
    "retell_agent_id": "agent_abc123",
    "has_retell_secret": true,
    "address": "123 Main St",
    "city": "San Francisco",
    "state": "CA",
    "phone": "+14155551234",
    "timezone": "America/Los_Angeles"
  }
]
```

---

## 3. Get Location

Get a specific location by slug.

*   **Endpoint**: `GET /admin/tenants/{slug}/locations/{loc_slug}`

**Response (200 OK):** Same schema as create response.

**Errors:**
- `404` — Tenant or location not found

---

## 4. Update Location

Update location fields. Uses PATCH semantics — omitted fields are ignored, sending `null` clears the value.

*   **Endpoint**: `PATCH /admin/tenants/{slug}/locations/{loc_slug}`

**Request Body (all fields optional):**
```json
{
  "name": "Acme Dental - Downtown (Updated)",
  "nexhealth_location_id": "999999",
  "retell_agent_id": null
}
```

| Field | Type | Notes |
|-------|------|-------|
| `name` | string | New display name |
| `is_active` | boolean | Activate/deactivate |
| `nexhealth_subdomain` | string | Override subdomain |
| `nexhealth_location_id` | string | Override location ID |
| `retell_agent_id` | string | New agent ID (send `null` to clear) |
| `retell_api_secret` | string | New secret (stored encrypted) |
| `address`, `city`, `state`, `phone`, `timezone` | string | Address fields |

**Response (200 OK):** Updated location object.

---

## 5. Delete Location

Delete a location (soft or hard delete).

*   **Endpoint**: `DELETE /admin/tenants/{slug}/locations/{loc_slug}`
*   **Query Parameters**:
    *   `hard` (boolean, default: `false`): If `true`, permanently deletes. Default is soft delete (`is_active=false`).

**Response (204 No Content)**

---

## 6. Sync Location

Trigger a PMS sync for a specific location. Fetches providers and appointment types from the PMS and caches them locally.

*   **Endpoint**: `POST /admin/tenants/{slug}/locations/{loc_slug}/sync`

**Response (200 OK):**
```json
{
  "location": "acme-downtown",
  "success": true,
  "providers_synced": 5,
  "appointment_types_synced": 12,
  "errors": []
}
```

**On partial failure:**
```json
{
  "location": "acme-downtown",
  "success": false,
  "providers_synced": 5,
  "appointment_types_synced": 0,
  "errors": ["Appointment type sync error: Connection timeout"]
}
```

---

## Using Location Context in API Requests

After creating a location, API clients can scope PMS queries to that location using the `X-Location-Slug` header:

```
GET /api/v1/pms/providers
X-Tenant-Slug: acme-dental
X-Location-Slug: acme-downtown
```

The middleware resolves the location and the PMS adapter uses the location's `nexhealth_subdomain` and `nexhealth_location_id` for the API call.

If `X-Location-Slug` is omitted, the tenant-level defaults are used (backward compatible).

---

## Example: Full Setup Flow

```bash
# 1. Create tenant (institution)
POST /admin/tenants
{
  "name": "Acme Dental Group",
  "slug": "acme-dental",
  "email": "admin@acmedental.com",
  "nexhealth_api_key": "your-api-key"
}

# 2. Create location A
POST /admin/tenants/acme-dental/locations
{
  "name": "Acme Downtown",
  "slug": "acme-downtown",
  "nexhealth_subdomain": "acme-practice",
  "nexhealth_location_id": "12345",
  "retell_agent_id": "agent_downtown"
}

# 3. Create location B
POST /admin/tenants/acme-dental/locations
{
  "name": "Acme Suburbs",
  "slug": "acme-suburbs",
  "nexhealth_subdomain": "acme-practice",
  "nexhealth_location_id": "67890",
  "retell_agent_id": "agent_suburbs"
}

# 4. Sync PMS data for each location
POST /admin/tenants/acme-dental/locations/acme-downtown/sync
POST /admin/tenants/acme-dental/locations/acme-suburbs/sync

# 5. Query providers for a specific location
GET /api/v1/pms/providers
X-Tenant-Slug: acme-dental
X-Location-Slug: acme-downtown
```
