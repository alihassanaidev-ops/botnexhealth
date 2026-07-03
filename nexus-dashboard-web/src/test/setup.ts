import "@testing-library/jest-dom/vitest"
import { afterEach, vi } from "vitest"
import { cleanup } from "@testing-library/react"

// jsdom lacks a few browser APIs that React Flow (and some Radix primitives) touch
// during render. Provide minimal, safe stubs so canvas-bearing components can mount
// in tests without throwing.
if (!("ResizeObserver" in globalThis)) {
    globalThis.ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
    } as unknown as typeof ResizeObserver
}
if (typeof globalThis.matchMedia !== "function") {
    globalThis.matchMedia = ((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
    })) as unknown as typeof globalThis.matchMedia
}
if (!("DOMMatrixReadOnly" in globalThis)) {
    globalThis.DOMMatrixReadOnly = class {
        m22 = 1
        constructor() {}
    } as unknown as typeof DOMMatrixReadOnly
}

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
