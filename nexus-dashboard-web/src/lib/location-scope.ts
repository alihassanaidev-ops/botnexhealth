/**
 * Active-location scope for multi-location location-scoped users.
 *
 * A LOCATION_ADMIN / STAFF account assigned several locations authenticates
 * pinned to its primary location; the backend accepts any assigned location
 * per-request via `?location_id=`. LocationContext publishes the current
 * selection here, and the api client's request interceptor attaches it to
 * every request that doesn't already carry a location — so each page follows
 * the sidebar selector without per-page plumbing.
 *
 * Deliberately null for institution admins and single-location users: their
 * requests are untouched and behave exactly as before.
 */

let activeLocationId: string | null = null

export function setActiveLocationScope(id: string | null): void {
    activeLocationId = id
}

export function getActiveLocationScope(): string | null {
    return activeLocationId
}
