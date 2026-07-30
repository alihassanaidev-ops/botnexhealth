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
// jsdom lacks these Element methods; TS's DOM lib already declares them, so the
// negated `in` branch narrows Element.prototype to `never`. Assign via a mutable
// cast so `tsc -b` (which type-checks test setup) doesn't fail the prod build.
const _elemProto = Element.prototype as unknown as Record<string, unknown>
if (!("hasPointerCapture" in Element.prototype)) {
  _elemProto.hasPointerCapture = () => false
}
if (!("setPointerCapture" in Element.prototype)) {
  _elemProto.setPointerCapture = () => {}
}
if (!("releasePointerCapture" in Element.prototype)) {
  _elemProto.releasePointerCapture = () => {}
}
if (!("scrollIntoView" in Element.prototype)) {
  _elemProto.scrollIntoView = () => {}
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
