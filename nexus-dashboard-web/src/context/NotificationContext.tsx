import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { useSSE } from "@/hooks/useSSE";
import type { Notification, NotificationBadgeCounts } from "@/types";
import {
    listNotifications,
    getUnreadCount,
    markAsRead as apiMarkAsRead,
    markAllAsRead as apiMarkAllAsRead,
    getNotificationIcon,
    isUrgent,
} from "@/lib/notifications-api";

const PAGE_SIZE = 50;

interface NotificationContextType {
    notifications: Notification[];
    totalNotifications: number;
    unreadCount: number;
    badgeCounts: NotificationBadgeCounts;
    isLoading: boolean;
    isDialogOpen: boolean;
    setIsDialogOpen: (open: boolean) => void;
    markAsRead: (id: string) => Promise<void>;
    markAllAsRead: () => Promise<void>;
    refreshNotifications: () => Promise<void>;
    loadMore: () => Promise<void>;
    hasMore: boolean;
}

const NotificationContext = createContext<NotificationContextType | undefined>(undefined);

export function NotificationProvider({ children }: { children: React.ReactNode }) {
    const { user } = useAuth();
    const { lastEvent } = useSSE();
    const notificationsEnabled = Boolean(user?.institution_id);
    const [notifications, setNotifications] = useState<Notification[]>([]);
    const [totalNotifications, setTotalNotifications] = useState(0);
    const [unreadCount, setUnreadCount] = useState(0);
    const [isLoading, setIsLoading] = useState(false);
    const [isDialogOpen, setIsDialogOpen] = useState(false);
    const [offset, setOffset] = useState(0);
    const [hasMore, setHasMore] = useState(false);

    const [badgeCounts, setBadgeCounts] = useState<NotificationBadgeCounts>({
        calls: 0,
        callbacks: 0,
        appointments: 0,
    });

    // Track previous unread count to detect new notifications for toasts
    const prevUnreadRef = useRef<number>(0);
    const hasHydratedUnreadRef = useRef(false);
    // Ref for notifications so polling interval doesn't depend on notifications state
    const notificationsRef = useRef<Notification[]>(notifications);
    useEffect(() => { notificationsRef.current = notifications; }, [notifications]);

    const showToast = useCallback((notification: Notification) => {
        const isUrgentNotification = isUrgent(notification.type);

        toast.custom(
            (t) => (
                <div
                    className={`
                        relative flex items-center gap-3 p-4 pr-12
                        bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700
                        rounded-lg shadow-lg cursor-pointer max-w-sm
                        ${isUrgentNotification ? "border-red-500 bg-red-50 dark:bg-red-950/50" : ""}
                    `}
                    onClick={() => {
                        setIsDialogOpen(true);
                        toast.dismiss(t);
                    }}
                >
                    <span className="text-zinc-600 dark:text-zinc-400">{getNotificationIcon(notification.type, "h-5 w-5")}</span>
                    <div className="flex-1 min-w-0">
                        <p className="font-medium text-zinc-900 dark:text-zinc-100 text-sm truncate">
                            {notification.title}
                        </p>
                        <p className="text-zinc-500 dark:text-zinc-400 text-xs truncate">
                            {notification.message}
                        </p>
                    </div>
                    <button
                        onClick={(e) => {
                            e.stopPropagation();
                            toast.dismiss(t);
                        }}
                        className="absolute right-2 top-2 text-zinc-400 hover:text-zinc-600 dark:text-zinc-500 dark:hover:text-zinc-300"
                    >
                        ×
                    </button>
                </div>
            ),
            {
                duration: isUrgentNotification ? Infinity : 5000,
                id: notification.id,
            }
        );
    }, []);

    // Fetch unread counts (lightweight, polled frequently)
    const refreshUnreadCounts = useCallback(async () => {
        if (!user || !notificationsEnabled) return;
        try {
            const counts = await getUnreadCount();
            const newTotal = counts.total;
            setUnreadCount(newTotal);
            setBadgeCounts({
                calls: counts.new_calls,
                callbacks: counts.callbacks,
                appointments: counts.appointments,
            });
            return newTotal;
        } catch {
            // Silently fail on poll — don't spam errors
        }
        return undefined;
    }, [user, notificationsEnabled]);

    // Fetch full notification list (heavier, on demand)
    const refreshNotifications = useCallback(async () => {
        if (!user || !notificationsEnabled) return;

        setIsLoading(true);
        try {
            const [listResult, unreadTotal] = await Promise.all([
                listNotifications(PAGE_SIZE, 0),
                refreshUnreadCounts(),
            ]);

            setNotifications(listResult.items);
            setTotalNotifications(listResult.total);
            setOffset(listResult.items.length);
            setHasMore(listResult.items.length < listResult.total);
            if (unreadTotal !== undefined) {
                prevUnreadRef.current = unreadTotal;
                hasHydratedUnreadRef.current = true;
            }
        } catch {
            // If API isn't available yet, just clear loading state
        } finally {
            setIsLoading(false);
        }
    }, [user, refreshUnreadCounts, notificationsEnabled]);

    // Load more notifications (pagination)
    const loadMore = useCallback(async () => {
        if (!user || !notificationsEnabled || !hasMore || isLoading) return;

        setIsLoading(true);
        try {
            const result = await listNotifications(PAGE_SIZE, offset);
            setNotifications((prev) => {
                // Deduplicate by id
                const existingIds = new Set(prev.map((n) => n.id));
                const newItems = result.items.filter((n) => !existingIds.has(n.id));
                return [...prev, ...newItems];
            });
            const newOffset = offset + result.items.length;
            setOffset(newOffset);
            setHasMore(newOffset < result.total);
        } catch {
            // Silently fail
        } finally {
            setIsLoading(false);
        }
    }, [user, hasMore, isLoading, offset, notificationsEnabled]);

    const markAsRead = useCallback(
        async (id: string) => {
            if (!notificationsEnabled) return;
            // Optimistic update
            setNotifications((prev) =>
                prev.map((n) => (n.id === id ? { ...n, is_read: true } : n))
            );
            setUnreadCount((prev) => {
                const next = Math.max(0, prev - 1);
                prevUnreadRef.current = next;
                hasHydratedUnreadRef.current = true;
                return next;
            });

            try {
                await apiMarkAsRead(id);
                // Refresh counts from server for accuracy
                const newTotal = await refreshUnreadCounts();
                if (newTotal !== undefined) {
                    prevUnreadRef.current = newTotal;
                    hasHydratedUnreadRef.current = true;
                }
            } catch {
                // Revert optimistic update on failure
                setNotifications((prev) =>
                    prev.map((n) => (n.id === id ? { ...n, is_read: false } : n))
                );
                const newTotal = await refreshUnreadCounts();
                if (newTotal !== undefined) {
                    prevUnreadRef.current = newTotal;
                    hasHydratedUnreadRef.current = true;
                }
            }
        },
        [refreshUnreadCounts, notificationsEnabled]
    );

    const markAllAsRead = useCallback(async () => {
        if (!notificationsEnabled) return;
        // Optimistic update
        setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
        setUnreadCount(0);
        setBadgeCounts({ calls: 0, callbacks: 0, appointments: 0 });
        prevUnreadRef.current = 0;
        hasHydratedUnreadRef.current = true;

        try {
            await apiMarkAllAsRead();
        } catch {
            // Revert on failure — re-fetch everything
            await refreshNotifications();
        }
    }, [refreshNotifications, notificationsEnabled]);

    // Initial fetch when user logs in
    useEffect(() => {
        if (user && notificationsEnabled) {
            refreshNotifications();
        } else {
            // Clear state on logout
            setNotifications([]);
            setUnreadCount(0);
            setBadgeCounts({ calls: 0, callbacks: 0, appointments: 0 });
            setTotalNotifications(0);
            setOffset(0);
            setHasMore(false);
            prevUnreadRef.current = 0;
            hasHydratedUnreadRef.current = false;
        }
    }, [user, refreshNotifications, notificationsEnabled]);

    const handleNotificationEvent = useCallback(async () => {
        try {
            const newTotal = await refreshUnreadCounts();
            if (newTotal === undefined) {
                return;
            }

            if (hasHydratedUnreadRef.current && newTotal > prevUnreadRef.current) {
                const result = await listNotifications(PAGE_SIZE, 0);
                const existingIds = new Set(notificationsRef.current.map((n) => n.id));
                const brandNew = result.items.filter(
                    (n) => !existingIds.has(n.id) && !n.is_read
                );

                for (const n of brandNew.slice(0, 3)) {
                    showToast(n);
                }

                setNotifications(result.items);
                setTotalNotifications(result.total);
                setOffset(result.items.length);
                setHasMore(result.items.length < result.total);
            }

            prevUnreadRef.current = newTotal;
            hasHydratedUnreadRef.current = true;
        } catch {
            // Silently fail on SSE refresh
        }
    }, [refreshUnreadCounts, showToast]);

    useEffect(() => {
        if (!user || !notificationsEnabled || lastEvent?.type !== "notification") return;
        void handleNotificationEvent();
    }, [handleNotificationEvent, lastEvent, notificationsEnabled, user]);

    // Refresh full list when dialog opens
    useEffect(() => {
        if (isDialogOpen && user && notificationsEnabled) {
            refreshNotifications();
        }
    }, [isDialogOpen, user, refreshNotifications, notificationsEnabled]);

    return (
        <NotificationContext.Provider
            value={{
                notifications,
                totalNotifications,
                unreadCount,
                badgeCounts,
                isLoading,
                isDialogOpen,
                setIsDialogOpen,
                markAsRead,
                markAllAsRead,
                refreshNotifications,
                loadMore,
                hasMore,
            }}
        >
            {children}
        </NotificationContext.Provider>
    );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useNotifications() {
    const context = useContext(NotificationContext);
    if (context === undefined) {
        throw new Error("useNotifications must be used within a NotificationProvider");
    }
    return context;
}
