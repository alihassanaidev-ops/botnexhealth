/**
 * In-memory storage adapter for Supabase Auth.
 *
 * Implements the SupportedStorage interface so Supabase never touches
 * localStorage or sessionStorage. Tokens exist only in JS memory and
 * are cleared automatically on tab close / refresh (HIPAA compliant).
 */

const store = new Map<string, string>();

export const secureStorage = {
    getItem(key: string): string | null {
        return store.get(key) ?? null;
    },
    setItem(key: string, value: string): void {
        store.set(key, value);
    },
    removeItem(key: string): void {
        store.delete(key);
    },
};
