import api from "@/lib/api";
import type { ReactNode } from "react";
import type { Notification, NotificationUnreadCount, NotificationType } from "@/types";


export interface NotificationsListResponse {
    items: Notification[];
    total: number;
    limit: number;
    offset: number;
}

export async function listNotifications(
    limit = 50,
    offset = 0
): Promise<NotificationsListResponse> {
    const { data } = await api.get<NotificationsListResponse>(
        `/institution/notifications?limit=${limit}&offset=${offset}`
    );
    return data;
}

export async function getUnreadCount(): Promise<NotificationUnreadCount> {
    const { data } = await api.get<NotificationUnreadCount>("/institution/notifications/unread-count");
    return data;
}

export async function markAsRead(notificationId: string): Promise<void> {
    await api.patch(`/institution/notifications/${notificationId}/read`);
}

export async function markAllAsRead(): Promise<void> {
    await api.post("/institution/notifications/mark-all-read");
}

import {
    Phone,
    ClipboardList,
    CheckCircle,
    Calendar,
    AlertTriangle,
    Bell
} from "lucide-react";

export function getNotificationIcon(type: NotificationType, className?: string): ReactNode {
    const cls = className ?? "h-5 w-5";
    switch (type) {
        case "new_call":
            return <Phone className={ cls } />;
        case "callback_item":
            return <ClipboardList className={ cls } />;
        case "callback_resolved":
            return <CheckCircle className={ cls } />;
        case "appointment_booked":
            return <Calendar className={ cls } />;
        case "urgent":
            return <AlertTriangle className={ cls } />;
        default:
            return <Bell className={ cls } />;
    }
}

export function getNotificationLabel(type: NotificationType): string {
    switch (type) {
        case "new_call":
            return "New Calls";
        case "callback_item":
            return "Callbacks";
        case "callback_resolved":
            return "Resolved";
        case "appointment_booked":
            return "Appointments";
        case "urgent":
            return "Urgent";
        default:
            return "Notifications";
    }
}

export function isUrgent(type: NotificationType): boolean {
    return type === "urgent";
}
