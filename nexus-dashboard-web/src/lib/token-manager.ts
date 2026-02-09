/**
 * In-memory backend JWT manager.
 *
 * The token lives only in a module-scoped variable — never persisted
 * to localStorage, sessionStorage, or cookies.
 */

let backendToken: string | null = null;

export function getToken(): string | null {
    return backendToken;
}

export function setToken(token: string): void {
    backendToken = token;
}

export function clearToken(): void {
    backendToken = null;
}
