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
        throw new Error("Supabase session refresh failed");
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
        const originalRequest = error.config;

        if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
            originalRequest._retry = true;

            try {
                const newBackendToken = await getRefreshedToken();
                // Retry original request
                originalRequest.headers.Authorization = `Bearer ${newBackendToken}`;
                return api(originalRequest);
            } catch {
                await forceSignOut();
            }
        }

        return Promise.reject(error);
    }
);

export default api;
