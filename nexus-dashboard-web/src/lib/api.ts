import axios from "axios";
import { supabase } from "@/lib/supabase";
import { getToken, setToken, clearToken } from "@/lib/token-manager";

const api = axios.create({
    baseURL: import.meta.env.VITE_API_URL || "http://localhost:3000/api",
    headers: {
        "Content-Type": "application/json",
    },
});

let refreshPromise: Promise<string> | null = null;
let signOutPromise: Promise<void> | null = null;

function redirectToLoginIfNeeded() {
    if (window.location.pathname !== "/login" && window.location.pathname !== "/set-password") {
        window.location.href = "/login";
    }
}

async function forceSignOut(): Promise<void> {
    if (!signOutPromise) {
        signOutPromise = (async () => {
            clearToken();
            await supabase.auth.signOut();
            redirectToLoginIfNeeded();
        })().finally(() => {
            signOutPromise = null;
        });
    }
    await signOutPromise;
}

async function refreshBackendToken(): Promise<string> {
    const { data, error: refreshError } = await supabase.auth.refreshSession();
    if (refreshError || !data.session) {
        const err = new Error("Supabase session refresh failed");
        (err as Error & { code?: string }).code = "NO_SUPABASE_SESSION";
        throw err;
    }

    const exchangeRes = await axios.post(
        `${api.defaults.baseURL}/auth/supabase/token`,
        { access_token: data.session.access_token },
        { headers: { "Content-Type": "application/json" } }
    );

    const newBackendToken: string = exchangeRes.data.access_token;
    setToken(newBackendToken);
    return newBackendToken;
}

function shouldForceLogoutFromRefreshError(error: unknown): boolean {
    const err = error as { code?: string; response?: { status?: number } };
    if (err?.code === "NO_SUPABASE_SESSION") return true;

    const status = err?.response?.status;
    return status === 401 || status === 403 || status === 423;
}

async function getRefreshedToken(): Promise<string> {
    if (!refreshPromise) {
        refreshPromise = refreshBackendToken().finally(() => {
            refreshPromise = null;
        });
    }
    return refreshPromise;
}

// Request interceptor — attach backend JWT
api.interceptors.request.use(
    (config) => {
        const token = getToken();
        if (token) {
            config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
    },
    (error) => Promise.reject(error)
);

// Response interceptor — handle 401 with Supabase refresh + re-exchange
api.interceptors.response.use(
    (response) => response,
    async (error) => {
        const originalRequest = error?.config;
        const requestUrl: string = String(originalRequest?.url ?? "");

        // Never recurse on token-exchange requests.
        if (requestUrl.includes("/auth/supabase/token")) {
            return Promise.reject(error);
        }

        if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
            originalRequest._retry = true;

            try {
                const newBackendToken = await getRefreshedToken();
                // Retry original request
                if (!originalRequest.headers) {
                    originalRequest.headers = {};
                }
                originalRequest.headers.Authorization = `Bearer ${newBackendToken}`;
                return api(originalRequest);
            } catch (refreshError) {
                // Only force logout for hard auth failures; keep session for transient failures.
                if (shouldForceLogoutFromRefreshError(refreshError)) {
                    await forceSignOut();
                }
                return Promise.reject(refreshError);
            }
        }

        return Promise.reject(error);
    }
);

export default api;
