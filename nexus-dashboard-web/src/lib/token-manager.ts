/**
 * In-memory auth token manager.
 *
 * Access and refresh tokens live only in module-scoped variables and are never
 * persisted to localStorage, sessionStorage, IndexedDB, or cookies.
 */

let accessToken: string | null = null;
let refreshToken: string | null = null;

export function getAccessToken(): string | null {
    return accessToken;
}

export function getRefreshToken(): string | null {
    return refreshToken;
}

export function setTokens(tokens: { accessToken: string; refreshToken: string }): void {
    accessToken = tokens.accessToken;
    refreshToken = tokens.refreshToken;
}

export function clearTokens(): void {
    accessToken = null;
    refreshToken = null;
}
