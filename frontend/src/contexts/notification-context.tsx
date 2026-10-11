"use client";

import * as React from "react";
import { useAuthStore } from "@/lib/auth";
import { openLiveSocket } from "@/lib/live-socket";
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
  // Signed in or not, rather than the token: the socket fetches the current
  // token itself, and a renewed one is no reason to reconnect.
  const signedIn = useAuthStore((s) => !!s.accessToken);
  const [notifications, setNotifications] = React.useState<UserNotification[]>(
    [],
  );
  const [unreadCount, setUnreadCount] = React.useState(0);
  const [isLoading, setIsLoading] = React.useState(true);

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

  React.useEffect(() => {
    if (!signedIn) {
      setNotifications([]);
      setUnreadCount(0);
      setIsLoading(false);
      return;
    }

    fetchInitial();

    return openLiveSocket("/api/v1/ws/notifications", {
      onMessage: (text) => {
        try {
          const notif: UserNotification = JSON.parse(text);
          setNotifications((prev) => [notif, ...prev].slice(0, 50));
          setUnreadCount((prev) => prev + 1);
        } catch {
          // Ignore malformed messages
        }
      },
    });
  }, [signedIn, fetchInitial]);

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
