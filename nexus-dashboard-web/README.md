# Dashboard (nexus-dashboard-web)

Clinic-staff web app: call list with transcripts/summaries/tags, callback
queue, daily metrics, institution/location administration, and account
security (MFA enrollment). Vite + React 19 + TypeScript, Tailwind + Radix UI,
React Router v7.

```bash
npm install
npm run dev        # API expected on http://localhost:8000 (set VITE_API_URL to override)
npm run test       # Vitest + testing-library
npm run build
```

## How it hangs together

- **Routing** — `src/router.tsx`. Lazy-loaded pages, role-gated routes
  (SUPER_ADMIN / INSTITUTION_ADMIN / LOCATION_ADMIN / STAFF).
- **Auth** — `src/context/AuthContext.tsx` + `src/lib/token-manager.ts`.
  Access token held in memory, refresh token in an HttpOnly cookie
  (`withCredentials` axios client in `src/lib/api.ts`, interceptor-driven
  refresh). 15-minute inactivity logout with a 60-second warning.
- **MFA** — login may return an MFA challenge instead of tokens; the flow
  handles TOTP and passkeys (`@simplewebauthn/browser`), plus a step-up dialog
  for sensitive actions. Factor management lives on the `/security` page.
- **Live updates** — SSE subscription; events are PHI-free hints
  (`calls_updated` etc.) and the app refetches through the API.

## Branding and deployment

Branding (HTML title, logo in `public/`) and the API target (`VITE_API_URL`)
are fixed at build time. The built app is published to S3 + CloudFront with
`make cdk-publish-frontend-staging` from the repo root.
