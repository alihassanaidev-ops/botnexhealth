# Frontend: Supabase Invite Flow Instructions

## Overview

When a new tenant is created, an invite email is sent via Supabase. The frontend must handle the redirect and allow the user to set their password.

---

## Authentication Flow

```
1. Admin creates tenant → Backend sends Supabase invite email
2. User clicks invite link in email
3. User redirected to frontend with tokens in URL fragment
4. Frontend detects invite → Shows "Set Password" screen
5. User sets password → Frontend calls Supabase updateUser
6. User can now log in normally
```

---

## Implementation Steps

### 1. Configure Deep Link Handling

- Set up GoRouter to handle the redirect URL from Supabase
- The URL will contain `#access_token=...&type=invite` in the fragment
- Add a route that can capture and parse this URL fragment

### 2. Detect Invite vs Normal Auth

- Check for `type=invite` in the URL fragment
- If present, the user is coming from an invite email
- Supabase will automatically authenticate them with the token

### 3. Create Set Password Screen

- Show a simple form with:
  - New Password field
  - Confirm Password field
  - Submit button
- Validate passwords match and meet minimum requirements (8+ characters recommended)

### 4. Call Supabase updateUser

- Use `supabase.auth.updateUser()` with the new password
- This sets the password for the invited user
- On success, redirect to dashboard

### 5. Handle Auth State with Riverpod

- Create a provider that watches Supabase auth state
- When user completes password setup, update the auth state
- Use the provider to guard protected routes

### 6. Access User Metadata

The Supabase JWT contains tenant information:
- `tenant_id` - The tenant this user belongs to
- `role` - User role ("TENANT")

Access via `supabase.auth.currentUser.userMetadata`

---

## Route Structure (GoRouter)

| Route | Purpose |
|-------|---------|
| `/` | Initial redirect handler for invite links |
| `/set-password` | Password setup screen for invited users |
| `/login` | Normal login screen |
| `/dashboard` | Protected dashboard (after auth) |

---

## Supabase Configuration Required

In **Supabase Dashboard → Authentication → URL Configuration**:

| Setting | Value |
|---------|-------|
| Site URL | Your frontend URL (e.g., `https://app.nexusdental.com`) |
| Redirect URLs | Same as Site URL |

---

## Testing

1. Ask backend to create a test tenant with your email
2. Check email for invite link
3. Click link → Should redirect to your app's set-password screen
4. Set password → Should complete and redirect to dashboard
5. Log out and log back in with email/password → Should work

---

## Error Handling

| Scenario | Action |
|----------|--------|
| Token expired (1 hour limit) | Show message asking user to request new invite |
| Password too weak | Show validation error, require stronger password |
| Network error | Show retry option |
| User already set password | Redirect to login instead |

---

## Notes

- The invite token expires after 1 hour
- User is temporarily authenticated when they click the link
- Password must be set before the session expires
- After password is set, user can log in normally with email/password
