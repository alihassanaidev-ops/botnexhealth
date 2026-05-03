/**
 * In-memory access-token store.
 *
 * The refresh token lives only in an HttpOnly cookie set by the backend, so it
 * is unreachable to JavaScript. Only the short-lived access token is held here.
 */

let accessToken: string | null = null;

export function getAccessToken(): string | null {
    return accessToken;
}

export function setAccessToken(token: string | null): void {
    accessToken = token;
}

export function clearAccessToken(): void {
    accessToken = null;
}
