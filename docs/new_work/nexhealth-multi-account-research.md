# NexHealth Multi-Account Research

Date: 2026-08-18

Question: can ScaleNexus support clinics using their own NexHealth account/API credentials instead of one shared platform key?

## Findings

- NexHealth authentication is API-key based. A caller posts to `/authenticates` with the API key in the `Authorization` header and receives a bearer token for subsequent API calls. Production tokens are valid for one hour; sandbox tokens are valid for 24 hours. Source: NexHealth Authentication docs, https://docs.nexhealth.com/reference/authentication-1.
- NexHealth rate limits are documented as applying to each API key. Defaults are 1,000 requests/minute for patient and appointment endpoints and 2,000 requests/minute for other endpoints. Source: NexHealth Rate limiting docs, https://docs.nexhealth.com/reference/rate-limiting.
- NexHealth institution-owned resources are scoped with `subdomain`. Webhook subscription creation requires `subdomain` for institution-owned resources, and the docs describe it as the institution scope. Source: Create webhook subscription docs, https://docs.nexhealth.com/reference/postwebhookendpointsidwebhooksubscriptions; v2 webhook setup docs, https://docs.nexhealth.com/v2.2.2/docs/webhooks.
- Webhook endpoints/subscriptions are owned by the authenticated API user/key. Subscription management can return 403/404 for resources outside the API user’s access, and moving subscriptions requires another endpoint owned by the same API user. Source: Edit webhook subscription docs, https://docs.nexhealth.com/reference/patchwebhookendpointsidwebhooksubscriptionssubscriptionid.

## Codebase State

- The repository currently uses one global NexHealth key from `settings.nexhealth_api_key`.
- `NexHealthAdapter.create()` explicitly says the platform shares a single NexHealth account and uses the global key.
- `institutions.nexhealth_api_key_encrypted` exists and admin create/update can store it, but the adapter path does not read it.
- Location-level isolation is already implemented with `InstitutionLocation.nexhealth_subdomain` and `InstitutionLocation.nexhealth_location_id`.

## Conclusion

Technically, the NexHealth API shape supports requests authenticated by different API keys, and the documented per-key rate limit means a multi-key implementation should be possible at the HTTP/client layer.

The unresolved business/vendor question is whether NexHealth will issue separate production API keys for each clinic/DSO and grant each key access to the relevant `subdomain`s under the account model ScaleNexus wants. The public docs show how API keys authenticate and scope access, but they do not explicitly promise a bring-your-own-clinic-account marketplace/connection model.

## Implementation Hurdles

1. Replace the global NexHealth client singleton with a keyed client/provider that can select `institution.nexhealth_api_key` when present and fall back to `settings.nexhealth_api_key`.
2. Token cache keys must include the API-key fingerprint. The current token manager cache is effectively one token slot, so multi-key auth would otherwise leak/reuse the wrong bearer token.
3. HTTP connection pooling and rate limiting should be per API key. The rate limiter already keys by API-key hash, but the global client only initializes one hash today.
4. Webhook endpoint/subscription records likely need an API-key/account identity or institution ownership column, because webhook endpoints are owned by the authenticated NexHealth API user.
5. Admin UX needs a verify/test-credentials action: authenticate with the clinic key, then check access to the configured `subdomain` and `location_id`.
6. Background jobs that enumerate NexHealth locations/subscriptions/backfills must group work by institution credential, not assume one platform account.

## Recommendation

Implement this as an optional per-institution credential path behind a conservative fallback:

- If `institution.nexhealth_api_key_encrypted` is present, use it for that institution’s NexHealth traffic.
- Otherwise use the existing platform key.
- Add credential verification before saving/enabling a clinic key.
- Ask NexHealth for written confirmation that separate clinic/DSO production API keys can be used by ScaleNexus for API access to those clinics' subdomains, and whether webhook endpoints/subscriptions are isolated per key.
