import axios from "axios";
import {
    clearAccessToken,
    getAccessToken,
    setAccessToken,
} from "@/lib/token-manager";

const api = axios.create({
    baseURL: import.meta.env.VITE_API_URL || "http://localhost:8000/api",
    headers: {
        "Content-Type": "application/json",
    },
    timeout: 30_000, // 30s default timeout to prevent hanging requests
    withCredentials: true,
});

let refreshPromise: Promise<string> | null = null;
let signOutPromise: Promise<void> | null = null;

interface AuthSessionResponse {
    access_token: string;
    token_type: string;
}

function redirectToLoginIfNeeded() {
    if (window.location.pathname !== "/login" && window.location.pathname !== "/set-password") {
        window.location.href = "/login";
    }
}

async function forceSignOut(): Promise<void> {
    if (!signOutPromise) {
        signOutPromise = (async () => {
            clearAccessToken();
            redirectToLoginIfNeeded();
        })().finally(() => {
            signOutPromise = null;
        });
    }
    await signOutPromise;
}

async function refreshBackendToken(): Promise<string> {
    const response = await axios.post<AuthSessionResponse>(
        `${api.defaults.baseURL}/auth/refresh`,
        {},
        {
            headers: { "Content-Type": "application/json" },
            timeout: 30_000,
            withCredentials: true,
        },
    );

    setAccessToken(response.data.access_token);
    return response.data.access_token;
}

function shouldForceLogoutFromRefreshError(error: unknown): boolean {
    const err = error as { response?: { status?: number } };
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
        const token = getAccessToken();
        if (token) {
            config.headers.Authorization = `Bearer ${token}`;
        }
        return config;
    },
    (error) => Promise.reject(error)
);

// Response interceptor — handle 401 with backend refresh-token rotation
api.interceptors.response.use(
    (response) => response,
    async (error) => {
        const originalRequest = error?.config;
        const requestUrl: string = String(originalRequest?.url ?? "");

        // Never recurse on auth bootstrap endpoints.
        if (
            requestUrl.includes("/auth/login")
            || requestUrl.includes("/auth/refresh")
            || requestUrl.includes("/auth/logout")
            || requestUrl.includes("/auth/set-password")
            || requestUrl.includes("/auth/reset-password")
            || requestUrl.includes("/auth/forgot-password")
        ) {
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
