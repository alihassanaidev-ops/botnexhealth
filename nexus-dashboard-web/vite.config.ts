/// <reference types="vitest/config" />
import path from "path"
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Make a Set-Cookie from the remote API usable on http://localhost.
 *
 * Two attributes have to go. `Domain=staging.scalenexus.ai` would make the
 * browser file the cookie under a host it is not talking to, so it would
 * never be sent back; dropping it makes the cookie host-only for localhost.
 * `Secure` is dropped because the dev server is plain http — Chrome grants
 * localhost a secure-context exemption but Safari does not, and stripping it
 * costs nothing on a loopback connection.
 *
 * SameSite is deliberately left alone. The browser only ever sees
 * localhost:3000 talking to localhost:3000, so even SameSite=Strict — what
 * the deployed backend sets on the refresh cookie — is satisfied.
 */
function cookieForLocalhost(cookie: string): string {
  return cookie
    .split(';')
    .filter((part) => {
      const attr = part.trim().toLowerCase()
      return attr !== 'secure' && !attr.startsWith('domain=')
    })
    .join(';')
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = env.VITE_PROXY_TARGET?.replace(/\/$/, '')

  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(process.cwd(), "./src"),
      },
    },
    server: {
      port: 3000,
      // When VITE_PROXY_TARGET is set, /api is forwarded to a deployed
      // backend instead of a local one. Going through the dev server rather
      // than letting the browser call the remote host directly is what makes
      // this work without touching the deployment: the page and the API share
      // an origin, so there is no CORS preflight, no cross-site cookie, and
      // no need to add localhost to the backend's allow-list.
      proxy: proxyTarget
        ? {
            '/api': {
              target: proxyTarget,
              changeOrigin: true,
              secure: true,
              configure: (proxy) => {
                proxy.on('proxyReq', (proxyReq) => {
                  // /auth/refresh and /auth/logout reject any request whose
                  // Origin is not in the backend's CORS allow-list. The
                  // browser sends localhost:3000; the API has to see itself.
                  proxyReq.setHeader('origin', proxyTarget)
                  proxyReq.setHeader('referer', `${proxyTarget}/`)
                })
                proxy.on('proxyRes', (proxyRes) => {
                  const setCookie = proxyRes.headers['set-cookie']
                  if (setCookie) {
                    proxyRes.headers['set-cookie'] = setCookie.map(cookieForLocalhost)
                  }
                })
              },
            },
          }
        : undefined,
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      css: false,
    },
  }
})
