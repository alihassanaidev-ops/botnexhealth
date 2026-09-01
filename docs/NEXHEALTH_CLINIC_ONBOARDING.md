# NexHealth Clinic Onboarding

This runbook explains how to onboard a clinic into Nexus when the PMS is
NexHealth.

Nexus supports two NexHealth credential modes:

- **Platform key**: the clinic is connected under the ScaleNexus/Nexus
  NexHealth developer account and uses the global `NEXHEALTH_API_KEY`.
- **Clinic-owned key**: the clinic or DSO has its own NexHealth developer
  account/API key. Nexus stores that key encrypted on the institution and uses
  it for that institution's NexHealth traffic.

In both modes, each physical office still needs a NexHealth `subdomain` and
`location_id`.

## How Request Routing Works

When Nexus needs NexHealth for patients, providers, appointment slots, booking,
webhooks, sync, or revalidation:

1. Nexus resolves the current `Institution`.
2. If `institutions.nexhealth_api_key_encrypted` is present, Nexus uses that
   clinic-owned key.
3. Otherwise Nexus uses the platform `NEXHEALTH_API_KEY`.
4. Nexus authenticates that selected key with NexHealth and caches the bearer
   token under a hash of that API key.
5. Nexus sends the actual NexHealth request with the selected bearer token plus
   the location's `nexhealth_subdomain` and `nexhealth_location_id`.

The token cache is per API key, so a clinic-owned key does not reuse the
platform token or another clinic's token.

## New Clinic Onboarding

### 1. Decide Credential Mode

Use **Platform key** when the clinic is onboarded under the Nexus/ScaleNexus
NexHealth developer account.

Use **Clinic-owned key** only when NexHealth has provided or approved a separate
developer/API key for that clinic or DSO.

### 2. Set Up NexHealth

In the correct NexHealth Developer Portal account:

1. Create or select the clinic's Institution.
2. Create the sync for the clinic's PMS database.
3. Install/configure the NexHealth Synchronizer:
   - server/on-prem PMS: install on the clinic server or hosted server
   - cloud PMS: provide the required credentials and install/authenticate the
     Chrome extension when NexHealth requires it
4. Confirm the sync creates the expected NexHealth location.
5. Collect:
   - API key, only for clinic-owned mode
   - `subdomain`
   - `location_id`

### 3. Create the Institution in Nexus

In Super Admin:

1. Create the Institution.
2. Set PMS type to `nexhealth`.
3. Open the Institution detail page.
4. Go to **Credentials**.
5. Choose credential mode:
   - **Platform key**: leave clinic API key disabled/empty.
   - **Clinic-owned key**: paste the clinic/DSO NexHealth API key.
6. For clinic-owned mode, click **Verify Key** before locations exist. This
   checks that the API key authenticates with NexHealth, but it does not prove
   access to a specific subdomain/location yet.
7. Save credentials.

### 4. Add the Location in Nexus

In Super Admin:

1. Go to **Locations**.
2. Add the physical office.
3. Use **Import from NexHealth** when available. The import list uses the
   institution's selected credential:
   - Platform key mode shows locations visible to the platform key.
   - Clinic-owned key mode shows locations visible to the clinic-owned key.
   - If a clinic-owned key is invalid, the import list should not show platform
     locations as a fallback.
4. Set or confirm:
   - `nexhealth_subdomain`
   - `nexhealth_location_id`
   - Retell agent ID, if voice is live
   - Twilio sender number, if SMS is live
5. Save the location.

For multi-location institutions, repeat this for every physical office. Nexus
requires explicit `location_id` on PMS-touching routes so it does not route a
booking into the wrong clinic.

### 5. Verify NexHealth Access

In Super Admin:

1. Open Institution detail.
2. Go to **Credentials**.
3. Select the credential mode.
4. Select a location under **Verify Against Location**.
5. Click **Verify**.

There are two verification levels:

- **Verify Key** appears when there is no local Nexus location selected yet.
  It authenticates with the selected NexHealth key only.
- **Verify** appears after a local Nexus location is available. It authenticates
  with the selected key, calls NexHealth `/locations` with the location's
  subdomain, and checks that the configured NexHealth `location_id` is visible
  to that key.

If verification fails:

- API/auth failure: the API key is missing, invalid, rotated, or not approved.
- Location not found: the key works, but does not have access to that
  `subdomain`/`location_id`, or the location values were copied incorrectly.

### 6. Create/Ensure Webhooks

After credentials and locations are verified, run the regular NexHealth webhook
subscription ensure job for the environment.

Webhook subscriptions are owned by the NexHealth API user/key that created them.
Nexus stores the credential mode and API-key hash on subscription rows so
operators can see which credential owns the remote webhook endpoint.

If a clinic changes from Platform key to Clinic-owned key later, treat webhooks
as a migration:

1. Verify the new clinic-owned key.
2. Create new webhook endpoint/subscriptions under the new key.
3. Confirm webhook deliveries arrive.
4. Deactivate old subscriptions using the old/platform key when possible.

Do not assume a webhook created by the platform key can be managed by the
clinic-owned key.

### 7. Verify Patient Browsing Separately

Open the clinic-facing Patients page with each location selected. The page reads
one current page directly from NexHealth through the backend, defaults to active
patients, and should expose working Next/Previous controls when NexHealth returns
more rows. This check proves current directory access; it does **not** prove that
NexHealth's Synchronizer has finished importing all historical PMS patients.

Webhook registration, Synchronizer read health, and historical projection
backfill are separate readiness signals. A clinic may browse the rows NexHealth
currently has while the local workflow projection is still catching up; do not
enable campaigns whose audience depends on a complete history until their
projection/reconciliation readiness check passes.

## Switching Credential Modes

### Platform Key to Clinic-Owned Key

1. Obtain the clinic-owned NexHealth API key.
2. In Super Admin, set credential mode to **Clinic-owned key**.
3. Paste the key and save.
4. Verify against each configured location.
5. Recreate or migrate webhook subscriptions under the new key.
6. Watch sync, booking, webhook, and automation logs for 403/404 errors.

### Clinic-Owned Key to Platform Key

1. In Super Admin, set credential mode to **Platform key**.
2. Save. Nexus clears the stored clinic-owned key.
3. Verify against each configured location using the platform key.
4. Migrate webhook subscriptions back under the platform key if needed.

## Operational Notes

- Rate limits are per NexHealth API key. Clinic-owned keys can separate rate
  limits only if NexHealth actually gives that clinic/DSO its own production
  key.
- Nexus never displays a saved NexHealth API key after storage.
- Stored clinic keys are encrypted in `institutions.nexhealth_api_key_encrypted`.
- The non-secret API-key hash may appear in logs or webhook subscription rows
  for routing/debugging.
- Background jobs, Retell function calls, sync, revalidation, and automation
  all use the same adapter path, so they follow the same credential selection
  rule.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Verify returns auth failure | Bad/rotated API key | Re-enter the key or confirm it in NexHealth Developer Portal |
| Verify authenticates but location not found | Key lacks access to subdomain/location | Confirm subdomain/location ID and NexHealth account access |
| Import list is empty in clinic-owned mode | Key is invalid or has no visible NexHealth locations | Run Verify Key, then confirm the NexHealth developer account has the clinic's institution/location |
| Booking returns 403 | Request is using a key without access to that location | Check credential mode and location mapping |
| Webhook ensure returns 403/404 | Endpoint/subscription was created under another API key | Manage it with the owning key or create a new subscription under the selected key |
| 429 rate limits continue | Clinic is still using platform key or NexHealth did not issue a separate key | Confirm credential mode and API-key ownership with NexHealth |
