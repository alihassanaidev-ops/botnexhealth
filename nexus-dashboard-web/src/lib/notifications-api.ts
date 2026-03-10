import api from "@/lib/api";
import type { Notification, NotificationUnreadCount, NotificationType } from "@/types";
import React from "react";

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
        `/notifications?limit=${limit}&offset=${offset}`
    );
    return data;
}

export async function getUnreadCount(): Promise<NotificationUnreadCount> {
    const { data } = await api.get<NotificationUnreadCount>("/notifications/unread-count");
    return data;
}

export async function markAsRead(notificationId: string): Promise<void> {
    await api.patch(`/notifications/${notificationId}/read`);
}

export async function markAllAsRead(): Promise<void> {
    await api.post("/notifications/mark-all-read");
}

import {
    Phone,
    ClipboardList,
    CheckCircle,
    Calendar,
    AlertTriangle,
    Bell
} from "lucide-react";

export function getNotificationIcon(type: NotificationType): React.ElementType {
    switch (type) {
        case "new_call":
            return Phone;
        case "callback_item":
            return ClipboardList;
        case "callback_resolved":
            return CheckCircle;
        case "appointment_booked":
            return Calendar;
        case "urgent":
            return AlertTriangle;
        default:
            return Bell;
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
