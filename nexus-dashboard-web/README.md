# Dashboard (nexus-dashboard-web)

Clinic-staff web app: call list with transcripts/summaries/tags, callback
queue, daily metrics, institution/location administration, and account
security (MFA enrollment). Vite + React 19 + TypeScript, Tailwind + Radix UI,
React Router v7.

```bash
npm install
npm run dev          # API expected on http://localhost:8000 (set VITE_API_URL to override)
npm run dev:staging  # UI local, API + data from deployed staging (see below)
npm run test         # Vitest + testing-library
npm run build
```

## Working on the UI against staging

`npm run dev:staging` runs the UI locally with hot reload while every API call
goes to the deployed staging backend — real tenants, real calls, real
appointments, no local Postgres, Redis, or backend process.

It works by proxying rather than by calling staging directly from the browser.
`.env.staging` sets `VITE_API_URL=/api`, so the app requests
`http://localhost:3000/api/...` and the Vite dev server forwards it. The
browser therefore sees a single origin, which sidesteps three things that
otherwise make this setup fail:

- **CORS** — staging's allow-list contains only `https://staging.scalenexus.ai`.
  A same-origin request never triggers a preflight, so nothing has to be added.
- **The refresh cookie** — it is `SameSite=Strict`, so a browser would refuse
  to send it from a `localhost` page to a `staging` host. Between
  `localhost:3000` and itself it is same-site and travels normally. The proxy
  strips `Secure` on the way back, since the dev server is plain http.
- **The CSRF check** — `/auth/refresh` and `/auth/logout` reject any request
  whose `Origin` is not on that same allow-list. The proxy rewrites `Origin`
  and `Referer` to the staging URL.

None of this needs a change or a redeploy on staging.

Two things to know before using it:

- **Passkeys will not work.** WebAuthn is bound to the staging hostname, so a
  passkey registered there cannot be used from `localhost` — the browser
  refuses the rpId, and the origin the authenticator signs into clientDataJSON
  is checked server-side against `WEBAUTHN_ALLOWED_ORIGINS`, so no proxy can
  forge it. The MFA screen defaults to passkey but offers **"Use authenticator
  code instead"** underneath; click that.
- **Writes are real.** Anything you save, send, or book goes into staging and
  can reach Twilio, Retell, or the PMS. Treat it as a shared environment.

Point `VITE_PROXY_TARGET` at another deployment to use the same workflow
elsewhere. Leaving it unset — which is what plain `npm run dev` does — disables
the proxy entirely and keeps the local-backend workflow unchanged.

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
