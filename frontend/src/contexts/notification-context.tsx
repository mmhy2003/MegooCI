"use client";

import * as React from "react";
import { useAuthStore } from "@/lib/auth";
import {
  userNotificationsApi,
  type UserNotification,
} from "@/lib/api";

interface NotificationContextValue {
  notifications: UserNotification[];
  unreadCount: number;
  isLoading: boolean;
  markRead: (id: string) => Promise<void>;
  markAllRead: () => Promise<void>;
  refresh: () => Promise<void>;
}

const NotificationContext = React.createContext<NotificationContextValue>({
  notifications: [],
  unreadCount: 0,
  isLoading: true,
  markRead: async () => {},
  markAllRead: async () => {},
  refresh: async () => {},
});

export function useNotifications() {
  return React.useContext(NotificationContext);
}

export function NotificationProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const accessToken = useAuthStore((s) => s.accessToken);
  const [notifications, setNotifications] = React.useState<UserNotification[]>(
    [],
  );
  const [unreadCount, setUnreadCount] = React.useState(0);
  const [isLoading, setIsLoading] = React.useState(true);
  const wsRef = React.useRef<WebSocket | null>(null);
  const reconnectTimerRef = React.useRef<ReturnType<typeof setTimeout>>(
    undefined,
  );

  const fetchInitial = React.useCallback(async () => {
    try {
      const [items, countRes] = await Promise.all([
        userNotificationsApi.list({ limit: 20 }),
        userNotificationsApi.unreadCount(),
      ]);
      setNotifications(items);
      setUnreadCount(countRes.count);
    } catch {
      // Silently fail — user may not be authenticated yet
    } finally {
      setIsLoading(false);
    }
  }, []);

  const connectWs = React.useCallback(() => {
    if (!accessToken || typeof window === "undefined") return;

    const tokenParam = `token=${encodeURIComponent(accessToken)}`;
    const apiBase = process.env.NEXT_PUBLIC_API_URL || "";
    let url: string;
    if (apiBase) {
      const parsed = new URL(apiBase);
      const wsProto = parsed.protocol === "https:" ? "wss:" : "ws:";
      url = `${wsProto}//${parsed.host}/api/v1/ws/notifications?${tokenParam}`;
    } else {
      const wsProto =
        window.location.protocol === "https:" ? "wss:" : "ws:";
      url = `${wsProto}//${window.location.host}/api/v1/ws/notifications?${tokenParam}`;
    }

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      try {
        const notif: UserNotification = JSON.parse(event.data);
        setNotifications((prev) => [notif, ...prev].slice(0, 50));
        setUnreadCount((prev) => prev + 1);
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = () => {
      wsRef.current = null;
      if (accessToken) {
        reconnectTimerRef.current = setTimeout(connectWs, 5000);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [accessToken]);

  React.useEffect(() => {
    if (!accessToken) {
      setNotifications([]);
      setUnreadCount(0);
      setIsLoading(false);
      return;
    }

    fetchInitial();
    connectWs();

    return () => {
      clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [accessToken, fetchInitial, connectWs]);

  const markRead = React.useCallback(async (id: string) => {
    try {
      const updated = await userNotificationsApi.markRead(id);
      setNotifications((prev) =>
        prev.map((n) => (n.id === id ? updated : n)),
      );
      setUnreadCount((prev) => Math.max(0, prev - 1));
    } catch {
      // Ignore errors
    }
  }, []);

  const markAllRead = React.useCallback(async () => {
    try {
      await userNotificationsApi.markAllRead();
      setNotifications((prev) =>
        prev.map((n) => ({
          ...n,
          read_at: n.read_at || new Date().toISOString(),
        })),
      );
      setUnreadCount(0);
    } catch {
      // Ignore errors
    }
  }, []);

  const value = React.useMemo<NotificationContextValue>(
    () => ({
      notifications,
      unreadCount,
      isLoading,
      markRead,
      markAllRead,
      refresh: fetchInitial,
    }),
    [notifications, unreadCount, isLoading, markRead, markAllRead, fetchInitial],
  );

  return (
    <NotificationContext.Provider value={value}>
      {children}
    </NotificationContext.Provider>
  );
}
