import axios from "axios";
import { supabase } from "@/lib/supabase";
import { getToken, setToken, clearToken } from "@/lib/token-manager";

const api = axios.create({
    baseURL: import.meta.env.VITE_API_URL || "http://localhost:3000/api",
    headers: {
        "Content-Type": "application/json",
    },
});

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

        if (error.response?.status === 401 && !originalRequest._retry) {
            originalRequest._retry = true;

            try {
                // Attempt Supabase session refresh
                const { data, error: refreshError } = await supabase.auth.refreshSession();

                if (refreshError || !data.session) {
                    clearToken();
                    await supabase.auth.signOut();
                    if (window.location.pathname !== "/login" && window.location.pathname !== "/set-password") {
                        window.location.href = "/login";
                    }
                    return Promise.reject(error);
                }

                // Re-exchange for a fresh backend JWT
                const exchangeRes = await axios.post(
                    `${api.defaults.baseURL}/auth/supabase/token`,
                    { access_token: data.session.access_token },
                    { headers: { "Content-Type": "application/json" } }
                );

                const newBackendToken: string = exchangeRes.data.access_token;
                setToken(newBackendToken);

                // Retry original request
                originalRequest.headers.Authorization = `Bearer ${newBackendToken}`;
                return api(originalRequest);
            } catch {
                clearToken();
                await supabase.auth.signOut();
                if (window.location.pathname !== "/login" && window.location.pathname !== "/set-password") {
                    window.location.href = "/login";
                }
            }
        }

        return Promise.reject(error);
    }
);

export default api;
