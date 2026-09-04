/**
 * Which visual treatment the app wears.
 *
 * The 2026 refresh changed the type scale, corner radii, dark palette and
 * swapped the lucide glyphs for illustrated artwork. All of that is reversible
 * at runtime, so the decision to keep it does not have to be made before it
 * ships and does not depend on reverting a commit — by the time anyone wants
 * to compare, the branch it came from will be buried under other work, and
 * rolling back to a tag would drag unrelated changes with it.
 *
 * What this does NOT revert: refactors with no visual opinion, like the shared
 * DateRangeFilter or Campaigns finally using PageHeader. Those are not part of
 * "the old UI" in any sense worth preserving.
 *
 * Resolution order, most specific first:
 *   1. ?ui=classic / ?ui=refresh in the URL — pins the choice and remembers it,
 *      so anyone can compare on the deployed site without a rebuild.
 *   2. Whatever was last pinned in this browser.
 *   3. VITE_UI_MODE at build time — the deployment-wide default.
 */
export type UiMode = "refresh" | "classic"

const STORAGE_KEY = "nexus.ui-mode"
const MODES: readonly string[] = ["refresh", "classic"]

function buildDefault(): UiMode {
    const configured = import.meta.env.VITE_UI_MODE ?? ""
    return MODES.includes(configured) ? (configured as UiMode) : "refresh"
}

function fromQuery(): UiMode | null {
    try {
        const value = new URLSearchParams(window.location.search).get("ui")
        return MODES.includes(value ?? "") ? (value as UiMode) : null
    } catch {
        return null
    }
}

function fromStorage(): UiMode | null {
    // Wrapped because storage throws outright in some privacy modes rather
    // than returning null.
    try {
        const value = localStorage.getItem(STORAGE_KEY)
        return MODES.includes(value ?? "") ? (value as UiMode) : null
    } catch {
        return null
    }
}

function remember(mode: UiMode): void {
    try {
        localStorage.setItem(STORAGE_KEY, mode)
    } catch {
        // Not being able to remember the choice is survivable; the query
        // parameter still applies for this page view.
    }
}

function resolve(): UiMode {
    const pinned = fromQuery()
    if (pinned) {
        remember(pinned)
        return pinned
    }
    return fromStorage() ?? buildDefault()
}

/**
 * Settled once at startup. Switching modes is a reload — the alternative is
 * threading reactivity through every component that renders an icon, to
 * support a control that gets used a handful of times while a decision is
 * being made.
 */
export const uiMode: UiMode = typeof window === "undefined" ? buildDefault() : resolve()

export function isClassicUi(): boolean {
    return uiMode === "classic"
}

/** Stamp the mode on <html> so the stylesheet can key off it. */
export function applyUiMode(): void {
    if (typeof document !== "undefined") {
        document.documentElement.dataset.ui = uiMode
    }
}
