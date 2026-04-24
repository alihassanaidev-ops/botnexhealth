import { useEffect, useState } from "react";

import { useAuth } from "@/context/AuthContext";
import api from "@/lib/api";

export type SSEEventType =
    | "calls_updated"
    | "callbacks_updated"
    | "dashboard_updated"
    | "notification";

export type SSEConnectionState =
    | "idle"
    | "connecting"
    | "connected"
    | "reconnecting";

export interface SSEEvent {
    type: SSEEventType;
    data: Record<string, unknown>;
    receivedAt: number;
}

const EVENT_TYPES: SSEEventType[] = [
    "calls_updated",
    "callbacks_updated",
    "dashboard_updated",
    "notification",
];

const INITIAL_RECONNECT_DELAY_MS = 1_000;
const MAX_RECONNECT_DELAY_MS = 30_000;

type EventListener = (event: SSEEvent) => void;
type StateListener = (state: SSEConnectionState) => void;

const eventListeners = new Set<EventListener>();
const stateListeners = new Set<StateListener>();

let sharedEventSource: EventSource | null = null;
let reconnectTimer: number | null = null;
let reconnectAttempt = 0;
let subscriberCount = 0;
let currentState: SSEConnectionState = "idle";

function emitState(state: SSEConnectionState) {
    currentState = state;
    for (const listener of stateListeners) {
        listener(state);
    }
}

function emitEvent(event: SSEEvent) {
    for (const listener of eventListeners) {
        listener(event);
    }
}

function clearReconnectTimer() {
    if (!reconnectTimer) return;
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
}

function closeSharedEventSource() {
    if (!sharedEventSource) return;
    sharedEventSource.close();
    sharedEventSource = null;
}

function buildSSEUrl(ticket: string): string {
    const baseURL = String(api.defaults.baseURL ?? "/api").replace(/\/$/, "");
    const url = baseURL.startsWith("http")
        ? new URL(`${baseURL}/institution/events`)
        : new URL(`${baseURL}/institution/events`, window.location.origin);

    url.searchParams.set("ticket", ticket);
    return url.toString();
}

async function fetchTicket(): Promise<string | null> {
    try {
        const response = await api.post<{ ticket: string }>(
            "/institution/events/ticket",
        );
        return response.data.ticket;
    } catch {
        return null;
    }
}

function dispatchEvent(type: SSEEventType, message: MessageEvent<string>) {
    let data: Record<string, unknown> = {};

    if (message.data) {
        try {
            const parsed = JSON.parse(message.data);
            if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
                data = parsed as Record<string, unknown>;
            }
        } catch {
            data = {};
        }
    }

    emitEvent({
        type,
        data,
        receivedAt: Date.now(),
    });
}

function scheduleReconnect() {
    clearReconnectTimer();

    if (subscriberCount === 0) {
        emitState("idle");
        return;
    }

    const delay = Math.min(
        INITIAL_RECONNECT_DELAY_MS * (2 ** reconnectAttempt),
        MAX_RECONNECT_DELAY_MS,
    );
    reconnectAttempt += 1;
    emitState("reconnecting");

    reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        void connectSharedEventSource();
    }, delay);
}

async function connectSharedEventSource() {
    clearReconnectTimer();

    if (subscriberCount === 0) {
        emitState("idle");
        return;
    }

    emitState(reconnectAttempt === 0 ? "connecting" : "reconnecting");

    const ticket = await fetchTicket();

    // All subscribers may have detached while we awaited the ticket — bail
    // instead of opening a zombie EventSource that nobody listens to.
    if (subscriberCount === 0) {
        emitState("idle");
        return;
    }

    if (!ticket) {
        scheduleReconnect();
        return;
    }

    closeSharedEventSource();

    const source = new EventSource(buildSSEUrl(ticket));
    sharedEventSource = source;

    source.onopen = () => {
        if (sharedEventSource !== source) return;
        reconnectAttempt = 0;
        emitState("connected");
    };

    source.onerror = () => {
        if (sharedEventSource !== source) return;
        closeSharedEventSource();
        scheduleReconnect();
    };

    for (const eventType of EVENT_TYPES) {
        source.addEventListener(
            eventType,
            ((message: MessageEvent<string>) => {
                dispatchEvent(eventType, message);
            }) as EventListenerOrEventListenerObject,
        );
    }
}

function retainSharedConnection() {
    subscriberCount += 1;

    if (subscriberCount === 1) {
        reconnectAttempt = 0;
        void connectSharedEventSource();
    } else if (!sharedEventSource && !reconnectTimer) {
        void connectSharedEventSource();
    }
}

function releaseSharedConnection() {
    subscriberCount = Math.max(0, subscriberCount - 1);

    if (subscriberCount > 0) {
        return;
    }

    clearReconnectTimer();
    closeSharedEventSource();
    reconnectAttempt = 0;
    emitState("idle");
}

export function useSSE() {
    const { user } = useAuth();
    const [lastEvent, setLastEvent] = useState<SSEEvent | null>(null);
    const [connectionState, setConnectionState] = useState<SSEConnectionState>(currentState);

    useEffect(() => {
        if (!user?.institution_id) {
            setLastEvent(null);
            setConnectionState("idle");
            return;
        }

        const handleEvent = (event: SSEEvent) => {
            setLastEvent(event);
        };
        const handleState = (state: SSEConnectionState) => {
            setConnectionState(state);
        };

        eventListeners.add(handleEvent);
        stateListeners.add(handleState);
        retainSharedConnection();

        return () => {
            eventListeners.delete(handleEvent);
            stateListeners.delete(handleState);
            releaseSharedConnection();
        };
    }, [user?.institution_id]);

    return {
        lastEvent,
        connectionState,
    };
}
