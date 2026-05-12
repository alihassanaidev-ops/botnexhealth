import "@testing-library/jest-dom/vitest"
import { afterEach } from "vitest"
import { cleanup } from "@testing-library/react"

afterEach(() => {
  cleanup()
  // Some test environments (CI containers, custom global polyfills)
  // expose ``localStorage`` as a plain object without the full Storage
  // contract, so a bare ``localStorage.clear()`` throws "is not a
  // function" and the whole afterEach handler crashes — taking every
  // assertion message with it. Be defensive: only clear if the method
  // exists, and swallow any clear-time error so test failures still
  // surface their real cause.
  try {
    if (typeof globalThis.localStorage?.clear === "function") {
      globalThis.localStorage.clear()
    }
  } catch {
    /* ignore */
  }
})
