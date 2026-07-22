import { useState, useMemo } from "react";
import { formatDistanceToNow } from "date-fns";
import { Check, CheckCheck, ChevronDown, ChevronUp, X, Bell, BellOff, History, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { useNotifications } from "@/context/NotificationContext";
import { getNotificationIcon, getNotificationLabel, isUrgent } from "@/lib/notifications-api";
import { FormSkeleton } from "@/components/ui/skeletons";
import type { Notification, NotificationType } from "@/types";

const NOTIFICATION_ORDER: NotificationType[] = [
    "new_call",
    "callback_item",
    "callback_resolved",
    "appointment_booked",
    "urgent",
];

export function NotificationDialog() {
    const {
        notifications,
        totalNotifications,
        isDialogOpen,
        setIsDialogOpen,
        markAsRead,
        markAllAsRead,
        unreadCount,
        isLoading,
        loadMore,
        hasMore,
    } = useNotifications();

    const [expandedGroups, setExpandedGroups] = useState<Set<NotificationType>>(
        new Set()  // Start with all accordions closed
    );
    const [showAllMode, setShowAllMode] = useState(false);

    // Filter notifications based on mode
    const displayedNotifications = useMemo(() => {
        if (showAllMode) {
            return notifications; // Show all (read + unread)
        }
        return notifications.filter((n) => !n.is_read); // Show only unread
    }, [notifications, showAllMode]);

    const groupedNotifications = useMemo(() => {
        const groups: Record<NotificationType, Notification[]> = {
            new_call: [],
            callback_item: [],
            callback_resolved: [],
            appointment_booked: [],
            urgent: [],
        };

        displayedNotifications.forEach((n) => {
            if (groups[n.type]) {
                groups[n.type].push(n);
            }
        });

        return groups;
    }, [displayedNotifications]);

    const toggleGroup = (type: NotificationType) => {
        setExpandedGroups((prev) => {
            const next = new Set(prev);
            if (next.has(type)) {
                next.delete(type);
            } else {
                next.add(type);
            }
            return next;
        });
    };

    const getUnreadCountForType = (type: NotificationType) => {
        return groupedNotifications[type]?.filter((n) => !n.is_read).length || 0;
    };

    const getTotalCountForType = (type: NotificationType) => {
        return groupedNotifications[type]?.length || 0;
    };

    if (!isDialogOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-start justify-end">
            {/* Backdrop */}
            <div
                className="absolute inset-0 bg-black/50 dark:bg-black/50"
                onClick={() => {
                    setIsDialogOpen(false);
                    setShowAllMode(false);
                }}
            />

            {/* Dialog Panel */}
            <div className="relative z-10 w-full max-w-md h-full bg-white dark:bg-zinc-950 border-l border-zinc-200 dark:border-zinc-800 shadow-2xl animate-in slide-in-from-right duration-200 flex flex-col">
                {/* Header */}
                <div className="flex items-center justify-between p-4 border-b border-zinc-200 dark:border-zinc-800">
                    <div className="flex items-center gap-2">
                        <Bell className="h-5 w-5" />
                        <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">
                            {showAllMode ? "All Notifications" : "Notifications"}
                        </h2>
                        {unreadCount > 0 && !showAllMode && (
                            <span className="bg-red-500 text-white text-xs font-bold px-2 py-0.5 rounded-full">
                                {unreadCount}
                            </span>
                        )}
                    </div>
                    <div className="flex items-center gap-2">
                        {!showAllMode && unreadCount > 0 && (
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={markAllAsRead}
                                className="text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                            >
                                <CheckCheck className="h-4 w-4 mr-1" />
                                Mark all read
                            </Button>
                        )}
                        {showAllMode && (
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => setShowAllMode(false)}
                                className="text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                            >
                                Back to Unread
                            </Button>
                        )}
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => {
                                setIsDialogOpen(false);
                                setShowAllMode(false);
                            }}
                            className="text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                        >
                            <X className="h-5 w-5" />
                        </Button>
                    </div>
                </div>

                {/* Notification Groups */}
                <ScrollArea className="flex-1">
                    <div className="p-2">
                        {/* Loading State */}
                        {isLoading && notifications.length === 0 && (
                            <FormSkeleton rows={3} />
                        )}

                        {!isLoading && NOTIFICATION_ORDER.map((type) => {
                            const items = groupedNotifications[type];
                            const unread = getUnreadCountForType(type);
                            const total = getTotalCountForType(type);
                            const isExpanded = expandedGroups.has(type);
                            const label = getNotificationLabel(type);


                            if (items.length === 0) return null;

                            return (
                                <div key={type} className="mb-2">
                                    {/* Accordion Header */}
                                    <button
                                        onClick={() => toggleGroup(type)}
                                        className={cn(
                                            "w-full flex items-center justify-between p-3 rounded-lg transition-colors",
                                            "hover:bg-zinc-100 dark:hover:bg-zinc-900/50",
                                            type === "urgent" && unread > 0 && "bg-red-50 hover:bg-red-100 dark:bg-red-950/30 dark:hover:bg-red-950/50"
                                        )}
                                    >
                                        <div className="flex items-center gap-2">
                                            {getNotificationIcon(type, "h-5 w-5")}
                                            <span className="font-medium text-zinc-800 dark:text-zinc-200">{label}</span>
                                            {showAllMode ? (
                                                // Show total count in "Show All" mode
                                                total > 0 && (
                                                    <span className="text-xs font-medium px-1.5 py-0.5 rounded-full bg-zinc-200 dark:bg-zinc-700 text-zinc-600 dark:text-zinc-300">
                                                        {total}
                                                    </span>
                                                )
                                            ) : (
                                                // Show unread count in normal mode
                                                unread > 0 && (
                                                    <span className={cn(
                                                        "text-xs font-bold px-1.5 py-0.5 rounded-full",
                                                        type === "urgent"
                                                            ? "bg-red-500 text-white"
                                                            : "bg-violet-500 text-white"
                                                    )}>
                                                        {unread}
                                                    </span>
                                                )
                                            )}
                                        </div>
                                        {isExpanded ? (
                                            <ChevronUp className="h-4 w-4 text-zinc-400 dark:text-zinc-500" />
                                        ) : (
                                            <ChevronDown className="h-4 w-4 text-zinc-400 dark:text-zinc-500" />
                                        )}
                                    </button>

                                    {/* Accordion Content */}
                                    {isExpanded && (
                                        <div className="mt-1 space-y-1">
                                            {items.map((notification) => (
                                                <NotificationItem
                                                    key={notification.id}
                                                    notification={notification}
                                                    onMarkRead={markAsRead}
                                                    showAllMode={showAllMode}
                                                />
                                            ))}
                                        </div>
                                    )}
                                </div>
                            );
                        })}

                        {/* Load More Button */}
                        {showAllMode && hasMore && (
                            <div className="py-3 flex justify-center">
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={loadMore}
                                    disabled={isLoading}
                                    className="text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                                >
                                    {isLoading ? (
                                        <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                                    ) : null}
                                    Load more
                                </Button>
                            </div>
                        )}

                        {/* Empty State for Unread Mode */}
                        {!isLoading && !showAllMode && displayedNotifications.length === 0 && (
                            <div className="flex flex-col items-center justify-center py-12 text-zinc-500 dark:text-zinc-500">
                                <CheckCheck className="h-10 w-10 mb-3 text-green-500" />
                                <p className="text-sm font-medium">All caught up!</p>
                                <p className="text-xs text-zinc-400 dark:text-zinc-600 mt-1">
                                    No unread notifications
                                </p>
                            </div>
                        )}

                        {/* Empty State for Show All Mode */}
                        {!isLoading && showAllMode && displayedNotifications.length === 0 && (
                            <div className="flex flex-col items-center justify-center py-12 text-zinc-500 dark:text-zinc-500">
                                <BellOff className="h-10 w-10 mb-3 text-zinc-400" />
                                <p className="text-sm">No notifications yet</p>
                                <p className="text-xs text-zinc-400 dark:text-zinc-600 mt-1">
                                    New notifications will appear here
                                </p>
                            </div>
                        )}
                    </div>
                </ScrollArea>

                {/* Footer - Show All Button */}
                {!showAllMode && (
                    <div className="p-3 border-t border-zinc-200 dark:border-zinc-800">
                        <Button
                            variant="outline"
                            className="w-full"
                            onClick={() => setShowAllMode(true)}
                        >
                            <History className="h-4 w-4 mr-2" />
                            Show All Notifications
                            {totalNotifications > 0 && (
                                <span className="ml-2 text-xs text-zinc-500 dark:text-zinc-400">
                                    ({totalNotifications} total)
                                </span>
                            )}
                        </Button>
                    </div>
                )}
            </div>
        </div>
    );
}

interface NotificationItemProps {
    notification: Notification;
    onMarkRead: (id: string) => Promise<void>;
    showAllMode?: boolean;
}

function NotificationItem({ notification, onMarkRead, showAllMode = false }: NotificationItemProps) {
    const [isMarking, setIsMarking] = useState(false);

    const handleMarkRead = async () => {
        if (notification.is_read) return;
        setIsMarking(true);
        await onMarkRead(notification.id);
        setIsMarking(false);
    };

    const timeAgo = formatDistanceToNow(new Date(notification.created_at), {
        addSuffix: true,
    });

    const urgent = isUrgent(notification.type);

    return (
        <div
            className={cn(
                "relative flex items-start gap-3 p-3 rounded-lg transition-colors",
                "hover:bg-zinc-50 dark:hover:bg-zinc-900/30 group",
                !notification.is_read && "bg-zinc-50 dark:bg-zinc-900/50",
                urgent && !notification.is_read && "bg-red-50 dark:bg-red-950/20",
                showAllMode && notification.is_read && "opacity-60"
            )}
        >
            {/* Unread Indicator */}
            {!notification.is_read && (
                <div className="absolute left-1 top-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full bg-violet-500" />
            )}

            {/* Icon */}
            <span className="mt-0.5 text-zinc-500 dark:text-zinc-400">{getNotificationIcon(notification.type, "h-5 w-5")}</span>

            {/* Content */}
            <div className="flex-1 min-w-0">
                <p className={cn(
                    "text-sm font-medium",
                    urgent ? "text-red-600 dark:text-red-400" : "text-zinc-800 dark:text-zinc-200"
                )}>
                    {notification.title}
                </p>
                <p className="text-xs text-zinc-500 dark:text-zinc-500 line-clamp-2 mt-0.5">
                    {notification.message}
                </p>
                <div className="flex items-center gap-2 mt-1">
                    <p className="text-xs text-zinc-400 dark:text-zinc-600">{timeAgo}</p>
                    {showAllMode && notification.is_read && (
                        <span className="text-xs text-green-600 dark:text-green-400 flex items-center gap-1">
                            <Check className="h-3 w-3" />
                            Read
                        </span>
                    )}
                </div>
            </div>

            {/* Mark as Read Button - Only show in unread mode */}
            {!showAllMode && !notification.is_read && (
                <Button
                    variant="ghost"
                    size="sm"
                    onClick={handleMarkRead}
                    disabled={isMarking}
                    className="opacity-0 group-hover:opacity-100 transition-opacity h-7 px-2 text-xs text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                >
                    <Check className="h-3 w-3 mr-1" />
                    Mark
                </Button>
            )}
        </div>
    );
}
