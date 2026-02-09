import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import api from "@/lib/api";
import { User } from "@/types";
import { supabase } from "@/lib/supabase";
import { setToken, clearToken } from "@/lib/token-manager";
import { toast } from "sonner";
import { useNavigate, useLocation } from "react-router-dom";

const INACTIVITY_TIMEOUT_MS = 15 * 60 * 1000; // 15 minutes — HIPAA automatic logoff

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

    // ---- Token exchange helper ----
    const exchangeToken = useCallback(async (supabaseAccessToken: string): Promise<boolean> => {
        try {
            const { data } = await api.post<{ access_token: string }>("/auth/supabase/token", {
                access_token: supabaseAccessToken,
            });
            setToken(data.access_token);
            return true;
        } catch (err) {
            console.error("Token exchange failed", err);
            return false;
        }
    }, []);

    // ---- Fetch backend user profile ----
    const fetchUserProfile = useCallback(async (): Promise<boolean> => {
        try {
            const { data } = await api.get<User>("/auth/users/me");
            setUser(data);
            return true;
        } catch (error) {
            console.error("Failed to fetch user profile", error);
            return false;
        }
    }, []);

    // ---- Full sign-in flow: exchange + fetch ----
    const completeSignIn = useCallback(async (supabaseAccessToken: string): Promise<boolean> => {
        const exchanged = await exchangeToken(supabaseAccessToken);
        if (!exchanged) return false;
        return await fetchUserProfile();
    }, [exchangeToken, fetchUserProfile]);

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
        const hash = window.location.hash;
        const isInviteOrRecovery = hash && (hash.includes('type=invite') || hash.includes('type=recovery'));

        // Initial session check
        supabase.auth.getSession().then(async ({ data: { session } }) => {
            if (session?.user) {
                if (isInviteOrRecovery) {
                    navigate("/set-password");
                    setIsLoading(false);
                } else {
                    const ok = await completeSignIn(session.access_token);
                    if (!ok) {
                        toast.error("Failed to load user profile. Please log in again.");
                        await signOut();
                    }
                    setIsLoading(false);
                }
            } else {
                setIsLoading(false);
            }
        });

        // Auth state change listener
        const { data: { subscription } } = supabase.auth.onAuthStateChange(async (event, session) => {
            if (event === 'PASSWORD_RECOVERY') {
                navigate("/set-password");
            } else if (event === 'SIGNED_IN' && session?.user) {
                if (isInviteOrRecovery) {
                    navigate("/set-password");
                } else if (!user) {
                    const ok = await completeSignIn(session.access_token);
                    if (ok) {
                        const from = (location.state as Record<string, { pathname?: string }>)?.from?.pathname || "/";
                        if (location.pathname === "/login") {
                            navigate(from, { replace: true });
                        }
                    } else {
                        toast.error("Login failed. Please try again.");
                        await signOut();
                    }
                }
            } else if (event === 'SIGNED_OUT') {
                clearToken();
                setUser(null);
                navigate("/login");
            }
        });

        return () => subscription.unsubscribe();
    }, [navigate, location.pathname]);

    // ---- Sign in with email + password ----
    const signInWithSupabase = async (email: string, password: string) => {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) {
            console.error("Login error", error);
            toast.error(error.message);
            throw error;
        }
        // onAuthStateChange handles the rest
    };

    // ---- Update password (invite / recovery flow) ----
    const updatePassword = async (password: string) => {
        const { error } = await supabase.auth.updateUser({ password });
        if (error) throw error;
        toast.success("Password updated successfully");
        navigate("/");
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
