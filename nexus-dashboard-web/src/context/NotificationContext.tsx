import React, { createContext, useContext, useState, useCallback, useEffect } from "react";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import type { Notification, NotificationType, NotificationBadgeCounts } from "@/types";
import {
    listNotifications,
    getNotificationIcon,
    isUrgent,
} from "@/lib/notifications-api";

interface NotificationContextType {
    notifications: Notification[];
    unreadCount: number;
    badgeCounts: NotificationBadgeCounts;
    isLoading: boolean;
    isDialogOpen: boolean;
    setIsDialogOpen: (open: boolean) => void;
    markAsRead: (id: string) => Promise<void>;
    markAllAsRead: () => Promise<void>;
    refreshNotifications: () => Promise<void>;
    addNotification: (notification: Notification) => void;
}

const NotificationContext = createContext<NotificationContextType | undefined>(undefined);

export function NotificationProvider({ children }: { children: React.ReactNode }) {
    const { user } = useAuth();
    const [notifications, setNotifications] = useState<Notification[]>([]);
    const [unreadCount, setUnreadCount] = useState(0);
    const [isLoading, setIsLoading] = useState(false);
    const [isDialogOpen, setIsDialogOpen] = useState(false);

    const [badgeCounts, setBadgeCounts] = useState<NotificationBadgeCounts>({
        calls: 0,
        callbacks: 0,
        appointments: 0,
    });

    const calculateBadges = useCallback((notifs: Notification[]) => {
        const counts: NotificationBadgeCounts = {
            calls: 0,
            callbacks: 0,
            appointments: 0,
        };

        let totalUnread = 0;

        notifs.forEach((n) => {
            if (!n.is_read) {
                totalUnread++;
                switch (n.type) {
                    case "new_call":
                        counts.calls++;
                        break;
                    case "callback_item":
                        counts.callbacks++;
                        break;
                    case "appointment_booked":
                        counts.appointments++;
                        break;
                }
            }
        });

        setUnreadCount(totalUnread);
        setBadgeCounts(counts);
    }, []);

    const showToast = useCallback((notification: Notification) => {
        const IconComponent = getNotificationIcon(notification.type);
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
                    <IconComponent className="h-5 w-5 text-zinc-600 dark:text-zinc-400" />
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

    const refreshNotifications = useCallback(async () => {
        if (!user) return;

        setIsLoading(true);

        // MOCK DATA - Replace with actual API call when backend is ready
        const mockNotifications: Notification[] = [
            {
                id: "1",
                user_id: user.id,
                type: "new_call",
                title: "New call from John Doe",
                message: "Patient needs callback regarding appointment",
                is_read: false,
                created_at: new Date(Date.now() - 2 * 60 * 1000).toISOString(),
                data: { call_id: "call-123" }
            },
            {
                id: "2",
                user_id: user.id,
                type: "new_call",
                title: "New call from Maria Garcia",
                message: "Booking confirmed for cleaning",
                is_read: false,
                created_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
                data: { call_id: "call-124" }
            },
            {
                id: "3",
                user_id: user.id,
                type: "callback_item",
                title: "Callback needed - Insurance issue",
                message: "Patient called about insurance verification",
                is_read: false,
                created_at: new Date(Date.now() - 10 * 60 * 1000).toISOString(),
                data: { call_id: "call-125" }
            },
            {
                id: "4",
                user_id: user.id,
                type: "callback_item",
                title: "Follow up required",
                message: "Patient requested callback for treatment plan",
                is_read: false,
                created_at: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
                data: { call_id: "call-126" }
            },
            {
                id: "5",
                user_id: user.id,
                type: "appointment_booked",
                title: "Appointment booked - Cleaning",
                message: "John Doe scheduled for cleaning on Mar 15",
                is_read: true,
                created_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
                data: { appointment_id: "appt-123" }
            },
            {
                id: "6",
                user_id: user.id,
                type: "urgent",
                title: "Urgent: Complaint reported",
                message: "Patient reported billing issue - needs immediate attention",
                is_read: false,
                created_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
                data: { call_id: "call-127" }
            },
            {
                id: "7",
                user_id: user.id,
                type: "callback_resolved",
                title: "Callback resolved - Insurance verified",
                message: "Insurance verification completed for patient",
                is_read: false,
                created_at: new Date(Date.now() - 2.5 * 60 * 60 * 1000).toISOString(),
                data: { call_id: "call-125" }
            },
            {
                id: "8",
                user_id: user.id,
                type: "callback_resolved",
                title: "Callback resolved - Treatment confirmed",
                message: "Patient confirmed treatment plan details",
                is_read: true,
                created_at: new Date(Date.now() - 4 * 60 * 60 * 1000).toISOString(),
                data: { call_id: "call-126" }
            },
            {
                id: "9",
                user_id: user.id,
                type: "new_call",
                title: "Previous call logged",
                message: "Call from existing patient about rescheduling",
                is_read: true,
                created_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
                data: { call_id: "call-128" }
            },
        ];

        setNotifications(mockNotifications);
        calculateBadges(mockNotifications);
        setIsLoading(false);
    }, [user, calculateBadges]);

    const addNotification = useCallback(
        (notification: Notification) => {
            setNotifications((prev) => {
                const exists = prev.some((n) => n.id === notification.id);
                if (exists) return prev;
                return [notification, ...prev];
            });

            calculateBadges([notification, ...notifications]);

            // Show toast
            showToast(notification);

            // Vibrate bell animation - handled by parent component via badge change
        },
        [notifications, calculateBadges, showToast]
    );

    const markAsRead = useCallback(
        async (id: string) => {
            // MOCK - Update local state only
            setNotifications((prev) =>
                prev.map((n) => (n.id === id ? { ...n, is_read: true } : n))
            );

            const updated = notifications.map((n) => (n.id === id ? { ...n, is_read: true } : n));
            calculateBadges(updated);
        },
        [notifications, calculateBadges]
    );

    const markAllAsRead = useCallback(async () => {
        // MOCK - Update local state only
        setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
        setUnreadCount(0);
        setBadgeCounts({ calls: 0, callbacks: 0, appointments: 0 });
    }, []);

    // Initial fetch
    useEffect(() => {
        if (user) {
            refreshNotifications();
        }
    }, [user, refreshNotifications]);

    // Test function - call this to simulate new notification
    // Usage: window.dispatchEvent(new CustomEvent('test-notification'))
    useEffect(() => {
        const handler = () => {
            const testNotification: Notification = {
                id: `test-${Date.now()}`,
                user_id: user?.id || "",
                type: Math.random() > 0.5 ? "new_call" : "callback_item",
                title: Math.random() > 0.5 ? "Test: New call received" : "Test: Callback needed",
                message: "This is a test notification to verify the system works",
                is_read: false,
                created_at: new Date().toISOString(),
            };
            addNotification(testNotification);
        };

        window.addEventListener("test-notification", handler);
        return () => window.removeEventListener("test-notification", handler);
    }, [user, addNotification]);

    // Poll for new notifications every 30 seconds
    useEffect(() => {
        if (!user) return;

        const interval = setInterval(() => {
            refreshNotifications();
        }, 30000);

        return () => clearInterval(interval);
    }, [user, refreshNotifications]);

    return (
        <NotificationContext.Provider
            value={{
                notifications,
                unreadCount,
                badgeCounts,
                isLoading,
                isDialogOpen,
                setIsDialogOpen,
                markAsRead,
                markAllAsRead,
                refreshNotifications,
                addNotification,
            }}
        >
            {children}
        </NotificationContext.Provider>
    );
}

export function useNotifications() {
    const context = useContext(NotificationContext);
    if (context === undefined) {
        throw new Error("useNotifications must be used within a NotificationProvider");
    }
    return context;
}
