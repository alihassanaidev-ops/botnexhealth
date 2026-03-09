import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import axios from "axios";
import api from "@/lib/api";
import { User } from "@/types";
import { authBootstrapUrlSnapshot, supabase } from "@/lib/supabase";
import { setToken, clearToken } from "@/lib/token-manager";
import { toast } from "sonner";
import { useNavigate, useLocation } from "react-router-dom";

const INACTIVITY_TIMEOUT_MS = 15 * 60 * 1000; // 15 minutes — HIPAA automatic logoff
const EXCHANGE_MAX_ATTEMPTS = 3;
const EXCHANGE_RETRY_DELAY_MS = 250;

interface AuthContextType {
    user: User | null;
    isLoading: boolean;
    signInWithSupabase: (email: string, password: string) => Promise<void>;
    signOut: () => Promise<void>;
    updatePassword: (password: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
    const [user, setUser] = useState<User | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const navigate = useNavigate();
    const location = useLocation();
    const inactivityTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const signInFlowRef = useRef<Promise<boolean> | null>(null);
    const lastAuthFailureRef = useRef<string | null>(null);

    // Use refs so the onAuthStateChange callback always has current values
    const locationRef = useRef(location);
    const userRef = useRef(user);
    useEffect(() => { locationRef.current = location; }, [location]);
    useEffect(() => { userRef.current = user; }, [user]);

    const getAuthFlowType = useCallback((): string | null => {
        const hash = window.location.hash || authBootstrapUrlSnapshot.hash || "";
        const search = window.location.search || authBootstrapUrlSnapshot.search || "";

        const hashParams = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash);
        const searchParams = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);

        return hashParams.get("type") || searchParams.get("type");
    }, []);

    const hasInviteOrRecoveryHash = useCallback((): boolean => {
        const flowType = getAuthFlowType();
        return flowType === "invite" || flowType === "recovery";
    }, [getAuthFlowType]);

    const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

    // ---- Token exchange helper ----
    const exchangeToken = useCallback(async (supabaseAccessToken: string): Promise<boolean> => {
        const authUrl = `${api.defaults.baseURL}/auth/supabase/token`;
        lastAuthFailureRef.current = null;

        for (let attempt = 1; attempt <= EXCHANGE_MAX_ATTEMPTS; attempt += 1) {
            try {
                // Use plain axios here to avoid auth interceptor recursion during bootstrap.
                const { data } = await axios.post<{ access_token: string }>(
                    authUrl,
                    { access_token: supabaseAccessToken },
                    { headers: { "Content-Type": "application/json" } },
                );
                setToken(data.access_token);
                return true;
            } catch (err: any) {
                const status = err?.response?.status as number | undefined;
                const detail = err?.response?.data?.detail as string | undefined;
                const retryable = !status || status === 429 || status >= 500;
                console.error(`Token exchange failed (attempt ${attempt}/${EXCHANGE_MAX_ATTEMPTS})`, err);

                if (retryable && attempt < EXCHANGE_MAX_ATTEMPTS) {
                    await sleep(EXCHANGE_RETRY_DELAY_MS * attempt);
                    continue;
                }
                lastAuthFailureRef.current = detail || (status ? `Authentication failed (${status})` : "Authentication failed");
                return false;
            }
        }

        return false;
    }, []);

    // ---- Fetch backend user profile ----
    const fetchUserProfile = useCallback(async (): Promise<boolean> => {
        try {
            const { data } = await api.get<User>("/auth/users/me");
            setUser(data);
            return true;
        } catch (error: any) {
            console.error("Failed to fetch user profile", error);
            const status = error?.response?.status as number | undefined;
            const detail = error?.response?.data?.detail as string | undefined;
            lastAuthFailureRef.current = detail || (status ? `Failed to fetch user profile (${status})` : "Failed to fetch user profile");
            return false;
        }
    }, []);

    // ---- Full sign-in flow: exchange + fetch ----
    const completeSignIn = useCallback(async (supabaseAccessToken: string): Promise<boolean> => {
        const exchanged = await exchangeToken(supabaseAccessToken);
        if (!exchanged) return false;
        return await fetchUserProfile();
    }, [exchangeToken, fetchUserProfile]);

    const completeSignInSingleFlight = useCallback((supabaseAccessToken: string): Promise<boolean> => {
        if (!signInFlowRef.current) {
            signInFlowRef.current = completeSignIn(supabaseAccessToken).finally(() => {
                signInFlowRef.current = null;
            });
        }
        return signInFlowRef.current;
    }, [completeSignIn]);

    // ---- Sign out (clears everything) ----
    const signOut = useCallback(async () => {
        clearToken();
        setUser(null);
        await supabase.auth.signOut();
        navigate("/login");
    }, [navigate]);

    // ---- Inactivity auto-logout (HIPAA) ----
    const resetInactivityTimer = useCallback(() => {
        if (inactivityTimer.current) clearTimeout(inactivityTimer.current);
        inactivityTimer.current = setTimeout(async () => {
            toast.info("Session expired due to inactivity");
            await signOut();
        }, INACTIVITY_TIMEOUT_MS);
    }, [signOut]);

    useEffect(() => {
        if (!user) return;

        const events = ["mousedown", "keydown", "mousemove", "touchstart", "scroll"];
        const handler = () => resetInactivityTimer();

        events.forEach((e) => window.addEventListener(e, handler, { passive: true }));
        resetInactivityTimer(); // start the timer

        return () => {
            events.forEach((e) => window.removeEventListener(e, handler));
            if (inactivityTimer.current) clearTimeout(inactivityTimer.current);
        };
    }, [user, resetInactivityTimer]);

    // ---- Session restore on mount + auth state changes ----
    useEffect(() => {
        let cancelled = false;

        const bootstrapSession = async () => {
            const isInviteOrRecovery = hasInviteOrRecoveryHash();
            try {
                const { data: { session } } = await supabase.auth.getSession();
                if (cancelled) return;

                if (session?.user) {
                    if (isInviteOrRecovery) {
                        navigate("/set-password", { replace: true });
                    } else {
                        const ok = await completeSignInSingleFlight(session.access_token);
                        if (!ok) {
                            toast.error(lastAuthFailureRef.current || "Failed to load user profile. Please log in again.");
                            await signOut();
                        }
                    }
                }
            } catch (error: any) {
                console.error("Initial auth bootstrap failed", error);
                lastAuthFailureRef.current = "Failed to initialize session";
                clearToken();
                setUser(null);
            } finally {
                if (!cancelled) {
                    setIsLoading(false);
                }
            }
        };

        void bootstrapSession();

        // Auth state change listener
        const { data: { subscription } } = supabase.auth.onAuthStateChange(async (event, session) => {
            try {
                if (event === 'PASSWORD_RECOVERY') {
                    navigate("/set-password", { replace: true });
                } else if (event === 'SIGNED_IN' && session?.user) {
                    const inviteOrRecoveryNow = hasInviteOrRecoveryHash();
                    if (inviteOrRecoveryNow) {
                        if (locationRef.current.pathname !== "/set-password") {
                            navigate("/set-password", { replace: true });
                        }
                    } else if (!userRef.current) {
                        const ok = await completeSignInSingleFlight(session.access_token);
                        if (ok) {
                            const loc = locationRef.current;
                            const from = (loc.state as Record<string, { pathname?: string }>)?.from?.pathname || "/";
                            if (loc.pathname === "/login") {
                                navigate(from, { replace: true });
                            }
                        } else {
                            toast.error(lastAuthFailureRef.current || "Login failed. Please try again.");
                            await signOut();
                        }
                    }
                } else if (event === 'SIGNED_OUT') {
                    clearToken();
                    setUser(null);
                    navigate("/login");
                }
            } catch (error) {
                console.error("Auth state change handler failed", error);
            } finally {
                setIsLoading(false);
            }
        });

        return () => {
            cancelled = true;
            subscription.unsubscribe();
        };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [hasInviteOrRecoveryHash]);

    // ---- Sign in with email + password ----
    const signInWithSupabase = async (email: string, password: string) => {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) {
            console.error("Login error", error);
            toast.error(error.message);
            throw error;
        }
        // onAuthStateChange handles the rest (token exchange, navigate)
    };

    // ---- Update password (invite / recovery flow) ----
    const updatePassword = async (password: string) => {
        const { error } = await supabase.auth.updateUser({ password });
        if (error) throw error;

        // After password set, complete backend token exchange + profile fetch
        // so role redirect works immediately without forcing a manual re-login.
        const { data: { session } } = await supabase.auth.getSession();
        if (!session?.access_token) {
            throw new Error("Password updated, but session is missing. Please sign in again.");
        }

        const ok = await completeSignInSingleFlight(session.access_token);
        if (!ok) {
            throw new Error("Password updated, but sign-in bootstrap failed. Please sign in again.");
        }

        // Clear invite/recovery hash to prevent redirecting back to /set-password.
        if (window.location.hash) {
            window.history.replaceState(null, document.title, window.location.pathname);
        }

        toast.success("Password updated successfully");
        navigate("/", { replace: true });
    };

    return (
        <AuthContext.Provider value={{ user, isLoading, signInWithSupabase, signOut, updatePassword }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    const context = useContext(AuthContext);
    if (context === undefined) {
        throw new Error("useAuth must be used within an AuthProvider");
    }
    return context;
}
