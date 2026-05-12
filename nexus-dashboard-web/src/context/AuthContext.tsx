import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import axios from "axios";
import api from "@/lib/api";
import { User } from "@/types";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {
    clearAccessToken,
    getAccessToken,
    setAccessToken,
} from "@/lib/token-manager";
import { toast } from "sonner";
import { useLocation, useNavigate } from "react-router-dom";

const INACTIVITY_TIMEOUT_MS = 15 * 60 * 1000;
const SESSION_WARNING_MS = 60 * 1000;
const SESSION_WARNING_SECONDS = Math.ceil(SESSION_WARNING_MS / 1000);
const AUTH_REQUEST_TIMEOUT_MS = 12_000;

type PasswordFlow = "invite" | "reset";

interface AuthSessionResponse {
    access_token: string;
    token_type: string;
}

/**
 * MFA-bound login result. Source-of-truth shape lives in mfa-api.ts;
 * re-imported here so that signIn/updatePassword consumers (Login,
 * SetPassword, the step-up modal) all hold the same type and can pass
 * the challenge straight into <MfaFlow />.
 */
import type { MfaChallengeResponse as MfaChallengeResult } from "@/lib/mfa-api";

export type SignInResult =
    | { kind: "authenticated" }
    | { kind: "mfa_challenge"; challenge: MfaChallengeResult };

interface AuthContextType {
    user: User | null;
    isLoading: boolean;
    /**
     * Initial password step. Returns the resolved outcome so callers can
     * decide whether to navigate directly (`authenticated`) or render an
     * MFA setup/verify screen (`mfa_challenge`). Errors are toasted and
     * thrown so the form can surface them.
     */
    signIn: (email: string, password: string) => Promise<SignInResult>;
    /**
     * Apply a session returned by an MFA verification endpoint. Same
     * effect as the post-/login path: store access token, fetch profile,
     * navigate to the post-login destination.
     */
    completeAuthSession: (
        session: AuthSessionResponse & { recovery_codes?: string[] | null },
    ) => Promise<void>;
    requestPasswordReset: (email: string) => Promise<void>;
    signOut: () => Promise<void>;
    /**
     * Consume an invite (`set`) or reset (`reset`) token plus a new
     * password. Returns the same shape as signIn — the backend may
     * return an MfaChallengeResponse here too, in which case the caller
     * renders the MFA setup/verify UI. Errors are thrown for the form
     * to display.
     */
    updatePassword: (
        password: string,
        token: string,
        flow: PasswordFlow,
    ) => Promise<SignInResult>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

function getErrorMessage(error: unknown, fallback: string): string {
    if (axios.isAxiosError(error)) {
        const detail = error.response?.data?.detail;
        if (typeof detail === "string" && detail.trim()) {
            return detail;
        }
        if (typeof error.message === "string" && error.message.trim()) {
            return error.message;
        }
    }

    if (error instanceof Error && error.message.trim()) {
        return error.message;
    }

    return fallback;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
    const [user, setUser] = useState<User | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [isSessionWarningOpen, setIsSessionWarningOpen] = useState(false);
    const [sessionSecondsRemaining, setSessionSecondsRemaining] = useState(SESSION_WARNING_SECONDS);
    const navigate = useNavigate();
    const location = useLocation();
    const inactivityTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const warningTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const countdownTimer = useRef<ReturnType<typeof setInterval> | null>(null);

    const clearInactivityTimers = useCallback(() => {
        if (inactivityTimer.current) {
            clearTimeout(inactivityTimer.current);
            inactivityTimer.current = null;
        }
        if (warningTimer.current) {
            clearTimeout(warningTimer.current);
            warningTimer.current = null;
        }
        if (countdownTimer.current) {
            clearInterval(countdownTimer.current);
            countdownTimer.current = null;
        }
    }, []);

    const clearSessionState = useCallback(() => {
        clearInactivityTimers();
        clearAccessToken();
        setUser(null);
        setIsSessionWarningOpen(false);
        setSessionSecondsRemaining(SESSION_WARNING_SECONDS);
    }, [clearInactivityTimers]);

    const fetchUserProfile = useCallback(async (): Promise<User> => {
        const { data } = await api.get<User>("/auth/users/me", {
            timeout: AUTH_REQUEST_TIMEOUT_MS,
        });
        setUser(data);
        return data;
    }, []);

    const applyAuthSession = useCallback(
        async (session: AuthSessionResponse): Promise<User> => {
            setAccessToken(session.access_token);

            try {
                return await fetchUserProfile();
            } catch (error) {
                clearSessionState();
                throw error;
            }
        },
        [clearSessionState, fetchUserProfile],
    );

    const navigateAfterSignIn = useCallback(() => {
        const from = (
            location.state as { from?: { pathname?: string } } | null
        )?.from?.pathname;
        navigate(from || "/", { replace: true });
    }, [location.state, navigate]);

    const signOut = useCallback(async () => {
        const accessToken = getAccessToken();
        clearSessionState();

        try {
            await axios.post(
                `${api.defaults.baseURL}/auth/logout`,
                {},
                {
                    headers: {
                        "Content-Type": "application/json",
                        ...(accessToken
                            ? { Authorization: `Bearer ${accessToken}` }
                            : {}),
                    },
                    timeout: AUTH_REQUEST_TIMEOUT_MS,
                    withCredentials: true,
                },
            );
        } catch {
            if (import.meta.env.DEV) {
                console.warn("Logout request failed; local session was still cleared");
            }
        }

        navigate("/login", { replace: true });
    }, [clearSessionState, navigate]);

    const resetInactivityTimer = useCallback(() => {
        clearInactivityTimers();
        setIsSessionWarningOpen(false);
        setSessionSecondsRemaining(SESSION_WARNING_SECONDS);

        const warningDelay = Math.max(INACTIVITY_TIMEOUT_MS - SESSION_WARNING_MS, 0);

        warningTimer.current = setTimeout(() => {
            const warningStartedAt = Date.now();
            setIsSessionWarningOpen(true);
            setSessionSecondsRemaining(SESSION_WARNING_SECONDS);

            countdownTimer.current = setInterval(() => {
                const elapsedMs = Date.now() - warningStartedAt;
                const remainingMs = SESSION_WARNING_MS - elapsedMs;
                if (remainingMs <= 0) {
                    setSessionSecondsRemaining(0);
                    if (countdownTimer.current) {
                        clearInterval(countdownTimer.current);
                        countdownTimer.current = null;
                    }
                    return;
                }
                setSessionSecondsRemaining(Math.ceil(remainingMs / 1000));
            }, 1000);

            inactivityTimer.current = setTimeout(async () => {
                toast.info("Session expired due to inactivity");
                await signOut();
            }, SESSION_WARNING_MS);
        }, warningDelay);
    }, [clearInactivityTimers, signOut]);

    const handleStaySignedIn = useCallback(() => {
        toast.success("Session extended");
        resetInactivityTimer();
    }, [resetInactivityTimer]);

    useEffect(() => {
        if (!user) {
            clearInactivityTimers();
            setIsSessionWarningOpen(false);
            return;
        }

        let lastActivity = 0;
        const THROTTLE_MS = 30_000;
        const events = ["mousedown", "keydown", "mousemove", "touchstart", "scroll"];

        const handler = () => {
            const now = Date.now();
            if (now - lastActivity < THROTTLE_MS) {
                return;
            }
            lastActivity = now;
            resetInactivityTimer();
        };

        events.forEach((eventName) =>
            window.addEventListener(eventName, handler, { passive: true }),
        );
        resetInactivityTimer();

        return () => {
            events.forEach((eventName) => window.removeEventListener(eventName, handler));
            clearInactivityTimers();
        };
    }, [clearInactivityTimers, resetInactivityTimer, user]);

    useEffect(() => {
        let cancelled = false;

        const bootstrapSession = async () => {
            try {
                // Refresh cookie is HttpOnly so JS cannot peek at it. Hit the
                // profile endpoint; the response interceptor will rotate via
                // /auth/refresh on 401 if the cookie is present and valid.
                await fetchUserProfile();

                if (!cancelled && location.pathname === "/login") {
                    navigate("/", { replace: true });
                }
            } catch {
                if (!cancelled) {
                    clearSessionState();
                }
            } finally {
                if (!cancelled) {
                    setIsLoading(false);
                }
            }
        };

        void bootstrapSession();

        return () => {
            cancelled = true;
        };
    }, [clearSessionState, fetchUserProfile, location.pathname, navigate]);

    const signIn = useCallback(
        async (email: string, password: string): Promise<SignInResult> => {
            try {
                const { data } = await axios.post<
                    AuthSessionResponse | MfaChallengeResult
                >(
                    `${api.defaults.baseURL}/auth/login`,
                    { email, password },
                    {
                        headers: { "Content-Type": "application/json" },
                        timeout: AUTH_REQUEST_TIMEOUT_MS,
                        withCredentials: true,
                    },
                );

                if (
                    "status" in data &&
                    (data.status === "mfa_required" || data.status === "mfa_setup_required")
                ) {
                    return { kind: "mfa_challenge", challenge: data as MfaChallengeResult };
                }

                await applyAuthSession(data as AuthSessionResponse);
                navigateAfterSignIn();
                return { kind: "authenticated" };
            } catch (error) {
                const message = getErrorMessage(error, "Login failed");
                toast.error(message);
                throw error;
            }
        },
        [applyAuthSession, navigateAfterSignIn],
    );

    const completeAuthSession = useCallback(
        async (
            session: AuthSessionResponse & { recovery_codes?: string[] | null },
        ): Promise<void> => {
            await applyAuthSession(session);
            navigateAfterSignIn();
        },
        [applyAuthSession, navigateAfterSignIn],
    );

    const requestPasswordReset = useCallback(async (email: string) => {
        const normalizedEmail = email.trim().toLowerCase();
        if (!normalizedEmail) {
            throw new Error("Email is required");
        }

        try {
            await axios.post(
                `${api.defaults.baseURL}/auth/forgot-password`,
                {
                    email: normalizedEmail,
                    redirect_url: `${window.location.origin}/set-password`,
                },
                {
                    headers: { "Content-Type": "application/json" },
                    timeout: AUTH_REQUEST_TIMEOUT_MS,
                },
            );
        } catch (error) {
            throw new Error(getErrorMessage(error, "Failed to send reset email"));
        }
    }, []);

    const updatePassword = useCallback(
        async (
            password: string,
            token: string,
            flow: PasswordFlow,
        ): Promise<SignInResult> => {
            try {
                const endpoint =
                    flow === "reset" ? "/auth/reset-password" : "/auth/set-password";

                const { data } = await axios.post<
                    AuthSessionResponse | MfaChallengeResult
                >(
                    `${api.defaults.baseURL}${endpoint}`,
                    { token, password },
                    {
                        headers: { "Content-Type": "application/json" },
                        timeout: AUTH_REQUEST_TIMEOUT_MS,
                        withCredentials: true,
                    },
                );

                // Backend may return MFA challenge here too (the
                // password-reset and invite-acceptance flows now go
                // through MFA before issuing a session). Hand the
                // challenge back to the form so it can render the
                // setup/verify UI; only finish the success toast +
                // navigate when an actual session lands.
                if (
                    "status" in data &&
                    (data.status === "mfa_required" || data.status === "mfa_setup_required")
                ) {
                    return { kind: "mfa_challenge", challenge: data as MfaChallengeResult };
                }

                await applyAuthSession(data as AuthSessionResponse);
                toast.success(
                    flow === "reset"
                        ? "Password reset successfully"
                        : "Password set successfully",
                );
                navigate("/", { replace: true });
                return { kind: "authenticated" };
            } catch (error) {
                throw new Error(getErrorMessage(error, "Failed to update password"));
            }
        },
        [applyAuthSession, navigate],
    );

    return (
        <AuthContext.Provider
            value={{
                user,
                isLoading,
                signIn,
                completeAuthSession,
                requestPasswordReset,
                signOut,
                updatePassword,
            }}
        >
            {children}
            <Dialog
                open={isSessionWarningOpen}
                onOpenChange={(open) => {
                    if (open) {
                        setIsSessionWarningOpen(true);
                    }
                }}
            >
                <DialogContent
                    className="sm:max-w-md"
                    onEscapeKeyDown={(event) => event.preventDefault()}
                    onPointerDownOutside={(event) => event.preventDefault()}
                >
                    <DialogHeader>
                        <DialogTitle>Session expiring soon</DialogTitle>
                        <DialogDescription>
                            You&apos;ve been inactive for a while. Stay signed in to keep working, or you&apos;ll be
                            logged out automatically.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm">
                        Your session will end in <span className="font-semibold">{sessionSecondsRemaining}s</span>.
                    </div>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => void signOut()}>
                            Sign out now
                        </Button>
                        <Button onClick={handleStaySignedIn}>Stay signed in</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </AuthContext.Provider>
    );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
    const context = useContext(AuthContext);
    if (context === undefined) {
        throw new Error("useAuth must be used within an AuthProvider");
    }
    return context;
}
