import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from "react";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import type { Notification, NotificationBadgeCounts } from "@/types";
import {
    listNotifications,
    getUnreadCount,
    markAsRead as apiMarkAsRead,
    markAllAsRead as apiMarkAllAsRead,
    getNotificationIcon,
    isUrgent,
} from "@/lib/notifications-api";

const POLL_INTERVAL_MS = 20_000; // 20 seconds
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
        if (!user) return;
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
    }, [user]);

    // Fetch full notification list (heavier, on demand)
    const refreshNotifications = useCallback(async () => {
        if (!user) return;

        setIsLoading(true);
        try {
            const [listResult] = await Promise.all([
                listNotifications(PAGE_SIZE, 0),
                refreshUnreadCounts(),
            ]);

            setNotifications(listResult.items);
            setTotalNotifications(listResult.total);
            setOffset(listResult.items.length);
            setHasMore(listResult.items.length < listResult.total);
        } catch {
            // If API isn't available yet, just clear loading state
        } finally {
            setIsLoading(false);
        }
    }, [user, refreshUnreadCounts]);

    // Load more notifications (pagination)
    const loadMore = useCallback(async () => {
        if (!user || !hasMore || isLoading) return;

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
    }, [user, hasMore, isLoading, offset]);

    const markAsRead = useCallback(
        async (id: string) => {
            // Optimistic update
            setNotifications((prev) =>
                prev.map((n) => (n.id === id ? { ...n, is_read: true } : n))
            );
            setUnreadCount((prev) => Math.max(0, prev - 1));

            try {
                await apiMarkAsRead(id);
                // Refresh counts from server for accuracy
                await refreshUnreadCounts();
            } catch {
                // Revert optimistic update on failure
                setNotifications((prev) =>
                    prev.map((n) => (n.id === id ? { ...n, is_read: false } : n))
                );
                await refreshUnreadCounts();
            }
        },
        [refreshUnreadCounts]
    );

    const markAllAsRead = useCallback(async () => {
        // Optimistic update
        setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
        setUnreadCount(0);
        setBadgeCounts({ calls: 0, callbacks: 0, appointments: 0 });

        try {
            await apiMarkAllAsRead();
        } catch {
            // Revert on failure — re-fetch everything
            await refreshNotifications();
        }
    }, [refreshNotifications]);

    // Initial fetch when user logs in
    useEffect(() => {
        if (user) {
            refreshNotifications();
        } else {
            // Clear state on logout
            setNotifications([]);
            setUnreadCount(0);
            setBadgeCounts({ calls: 0, callbacks: 0, appointments: 0 });
            setOffset(0);
            setHasMore(false);
            prevUnreadRef.current = 0;
        }
    }, [user, refreshNotifications]);

    // Poll unread counts (lightweight) and detect new notifications
    useEffect(() => {
        if (!user) return;

        const poll = async () => {
            const newTotal = await refreshUnreadCounts();
            if (newTotal !== undefined && newTotal > prevUnreadRef.current && prevUnreadRef.current > 0) {
                // New notifications arrived — refresh the full list to get them
                const result = await listNotifications(PAGE_SIZE, 0);
                // Find truly new ones (not in current list) for toast
                const existingIds = new Set(notifications.map((n) => n.id));
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
            if (newTotal !== undefined) {
                prevUnreadRef.current = newTotal;
            }
        };

        const interval = setInterval(poll, POLL_INTERVAL_MS);
        return () => clearInterval(interval);
    }, [user, refreshUnreadCounts, notifications, showToast]);

    // Refresh full list when dialog opens
    useEffect(() => {
        if (isDialogOpen && user) {
            refreshNotifications();
        }
    }, [isDialogOpen, user, refreshNotifications]);

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
