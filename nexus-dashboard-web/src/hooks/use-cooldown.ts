import { useCallback, useEffect, useMemo, useState } from "react";

export function useCooldown(durationSeconds: number) {
    const [remaining, setRemaining] = useState(0);

    /**
     * Begin the countdown. Pass `overrideSeconds` when the server, rather
     * than the caller, decides the window — e.g. a resend endpoint that
     * returns its own retry delay. Without it the caller would have to
     * push that value into state first, and `start` would still be bound
     * to the previous duration for that render.
     */
    const start = useCallback(
        (overrideSeconds?: number) => {
            setRemaining(
                typeof overrideSeconds === "number" && overrideSeconds > 0
                    ? overrideSeconds
                    : durationSeconds,
            );
        },
        [durationSeconds]
    );

    const reset = useCallback(() => {
        setRemaining(0);
    }, []);

    useEffect(() => {
        if (remaining <= 0) return;
        const id = window.setInterval(() => {
            setRemaining((prev) => (prev <= 1 ? 0 : prev - 1));
        }, 1000);
        return () => window.clearInterval(id);
    }, [remaining]);

    return {
        remaining,
        isActive: remaining > 0,
        start,
        reset,
    };
}

export function useCooldownMap(durationSeconds: number) {
    const [cooldowns, setCooldowns] = useState<Record<string, number>>({});

    const start = useCallback(
        (key: string) => {
            setCooldowns((prev) => ({ ...prev, [key]: durationSeconds }));
        },
        [durationSeconds]
    );

    const reset = useCallback((key: string) => {
        setCooldowns((prev) => {
            if (!(key in prev)) return prev;
            const next = { ...prev };
            delete next[key];
            return next;
        });
    }, []);

    const hasActive = useMemo(
        () => Object.values(cooldowns).some((value) => value > 0),
        [cooldowns]
    );

    useEffect(() => {
        if (!hasActive) return;
        const id = window.setInterval(() => {
            setCooldowns((prev) => {
                let changed = false;
                const next: Record<string, number> = {};
                for (const [key, value] of Object.entries(prev)) {
                    const nextValue = value > 0 ? value - 1 : 0;
                    if (nextValue > 0) {
                        next[key] = nextValue;
                    }
                    if (nextValue !== value) {
                        changed = true;
                    }
                }
                return changed ? next : prev;
            });
        }, 1000);
        return () => window.clearInterval(id);
    }, [hasActive]);

    const getRemaining = useCallback(
        (key: string) => cooldowns[key] ?? 0,
        [cooldowns]
    );

    const isActive = useCallback(
        (key: string) => (cooldowns[key] ?? 0) > 0,
        [cooldowns]
    );

    return {
        start,
        reset,
        getRemaining,
        isActive,
    };
}
