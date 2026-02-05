# Admin Authentication - Frontend Integration Guide

## Overview

This backend provides a secure, HIPAA-compliant authentication system for admin users. All authentication follows OAuth2 standards with JWT (JSON Web Token) bearer tokens.

---

## Endpoints

### 1. Login

**`POST /auth/token`**

Authenticates a user and returns an access token.

| Parameter  | Type   | Location | Required |
|------------|--------|----------|----------|
| `username` | string | form     | Yes      |
| `password` | string | form     | Yes      |

**Content-Type:** `application/x-www-form-urlencoded`

**Response (200 OK):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

**Error Responses:**
- `401 Unauthorized` - Invalid credentials
- `422 Unprocessable Entity` - Missing required fields

---

### 2. Get Current User

**`GET /auth/users/me`**

Returns the profile of the currently authenticated user.

**Headers:**
```
Authorization: Bearer <access_token>
```

**Response (200 OK):**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "admin@example.com",
  "role": "ADMIN",
  "is_active": true
}
```

**Error Responses:**
- `401 Unauthorized` - Missing or invalid token

---

## Token Usage

### Storage
- Store the `access_token` securely (e.g., `httpOnly` cookie or secure storage).
- **Do NOT store tokens in localStorage** for HIPAA compliance—prefer session-based storage.

### Sending Authenticated Requests
Include the token in the `Authorization` header for all protected endpoints:
```
Authorization: Bearer <access_token>
```

### Token Expiration
- Tokens expire after **30 minutes**.
- When a `401` response is received, redirect the user to the login screen.
- Implement token refresh logic or re-authentication as needed.

---

## User Roles

| Role   | Description                     |
|--------|---------------------------------|
| ADMIN  | Full administrative access      |
| STAFF  | Limited access (future use)     |

The backend will return `403 Forbidden` for endpoints requiring ADMIN role if the logged-in user is STAFF.

---

## HIPAA Compliance Notes

1. **Audit Logging**: All login attempts (success and failure) are logged with timestamps, IP addresses, and outcomes.
2. **Password Security**: Passwords are hashed using bcrypt (never stored in plain text).
3. **Token Security**: Tokens are signed with HS256 and contain no PHI.
4. **Session Timeout**: Tokens expire after 30 minutes of inactivity.
5. **Failed Login Tracking**: Failed attempts are tracked for brute-force protection.

---

## Error Handling

| Status Code | Meaning                          | Action                        |
|-------------|----------------------------------|-------------------------------|
| 401         | Not authenticated / bad token    | Redirect to login             |
| 403         | Insufficient permissions         | Show "Access Denied" message  |
| 422         | Validation error                 | Show form validation errors   |
| 500         | Server error                     | Show generic error message    |

---

## Example Flow

1. **User enters credentials** → Frontend calls `POST /auth/token`
2. **Backend validates** → Returns `access_token`
3. **Frontend stores token** → Securely (httpOnly cookie recommended)
4. **User navigates app** → All API calls include `Authorization: Bearer <token>`
5. **Token expires** → Backend returns `401` → Frontend redirects to login

---

## Contact

For questions about authentication or access issues, contact the backend team.
