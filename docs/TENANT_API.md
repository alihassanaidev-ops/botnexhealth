# Tenant Management API Documentation

**Base URL**: `https://<api-domain>/admin/tenants`  
**Authentication**: Requires `Authorization: Bearer <JWT_TOKEN>` (User must have `ADMIN` role).

---

## 1. List All Tenants

Retrieve a list of all tenants.

*   **Endpoint**: `GET /admin/tenants`
*   **Query Parameters**:
    *   `include_inactive` (boolean, default: `false`): Set to `true` to include inactive tenants.

**Response (200 OK):**
```json
[
  {
    "id": "tenant-uuid-1",
    "name": "Acme Dental",
    "slug": "acme-dental",
    "is_active": true,
    "has_nexhealth_key": true,
    "has_ghl_key": false,
    ...
  },
  ...
]
```

---

## 2. Get Single Tenant

Retrieve details for a specific tenant by slug.

*   **Endpoint**: `GET /admin/tenants/{slug}`
*   **Path Parameters**:
    *   `slug`: The unique slug of the tenant (e.g., `acme-dental`).

**Response (200 OK):**
```json
{
  "id": "tenant-uuid-1",
  "name": "Acme Dental",
  "slug": "acme-dental",
  "is_active": true,
  "nexhealth_subdomain": "acme",
  "nexhealth_location_id": "12345",
  "ghl_location_id": null,
  "retell_agent_id": "agent_123",
  "has_nexhealth_key": true,
  "has_ghl_key": false,
  "has_retell_secret": true,
  "has_sikka_credentials": false
}
```

---

## 3. Create Tenant (and Initial User)

Create a new tenant with an initial user.

*   **Endpoint**: `POST /admin/tenants`
*   **Body (JSON)**:

```json
{
  "name": "New Clinic",
  "slug": "new-clinic",
  
  // Initial Tenant User (Mandatory)
  "email": "user@newclinic.com",
  // Password is NOT provided here. An invite email will be sent via Supabase.

  // Optional: Configuration & Credentials
  "nexhealth_api_key": "nh_key_...",
  "nexhealth_subdomain": "newclinic",
  "nexhealth_location_id": "123",
  
  "ghl_api_key": "ghl_key_...",
  "ghl_location_id": "loc_123",
  
  "retell_agent_id": "agent_xyz",
  "retell_api_secret": "secret_..."
}
```

**Response (201 Created):**
```json
{
  "id": "tenant-uuid-2",
  "name": "New Clinic",
  "slug": "new-clinic",
  "is_active": true,
  "user": {
    "id": "user-uuid-1",
    "email": "user@newclinic.com",
    "role": "TENANT",
    "is_active": true
  }
}
```

---

## 4. Update Tenant

Update an existing tenant's configuration or credentials. Only provided fields are updated.

*   **Endpoint**: `PATCH /admin/tenants/{slug}`
*   **Path Parameters**:
    *   `slug`: The unique slug of the tenant.
*   **Body (JSON)**:

```json
{
  "name": "New Clinic Renamed",
  "is_active": true,
  "nexhealth_api_key": "new_key_..."
}
```

**Response (200 OK):**
Returns the updated tenant object.

---

## 5. Delete Tenant

Delete a tenant.

*   **Endpoint**: `DELETE /admin/tenants/{slug}`
*   **Query Parameters**:
    *   `hard` (boolean, default: `false`): If `true`, performs a permanent hard delete. If `false` (default), usually performs a soft delete (implementation dependent).

**Response (204 No Content):**
Empty response body.
