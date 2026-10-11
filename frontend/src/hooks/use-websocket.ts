"use client";

import { useEffect, useState } from "react";
import { openLiveSocket } from "@/lib/live-socket";

export interface UseWebSocketReturn {
  messages: string[];
  isConnected: boolean;
}

/**
 * Collects every message from the API WebSocket at *path* (no host, no
 * token: `/api/v1/ws/...`). Pass null for no connection.
 */
export function useWebSocket(path: string | null): UseWebSocketReturn {
  const [messages, setMessages] = useState<string[]>([]);
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    if (!path) return;

    setMessages([]);
    const close = openLiveSocket(path, {
      onOpen: () => setIsConnected(true),
      onMessage: (data) => setMessages((prev) => [...prev, data]),
      onClose: () => setIsConnected(false),
    });

    return () => {
      close();
      setIsConnected(false);
    };
  }, [path]);

  return { messages, isConnected };
}
