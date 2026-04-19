"use client";

import { useEffect, useRef, useState, useCallback } from "react";

export interface UseWebSocketReturn {
  messages: string[];
  isConnected: boolean;
  send: (data: string) => void;
}

export function useWebSocket(url: string | null): UseWebSocketReturn {
  const [messages, setMessages] = useState<string[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const urlRef = useRef(url);
  urlRef.current = url;

  const connect = useCallback(() => {
    if (!urlRef.current) return;

    const ws = new WebSocket(urlRef.current);
    wsRef.current = ws;

    ws.onopen = () => setIsConnected(true);

    ws.onmessage = (event) => {
      setMessages((prev) => [...prev, event.data]);
    };

    ws.onclose = () => {
      setIsConnected(false);
      wsRef.current = null;
      if (urlRef.current) {
        reconnectTimerRef.current = setTimeout(connect, 3000);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }, []);

  useEffect(() => {
    if (!url) {
      wsRef.current?.close();
      wsRef.current = null;
      setIsConnected(false);
      return;
    }

    setMessages([]);
    connect();

    return () => {
      clearTimeout(reconnectTimerRef.current);
      urlRef.current = null;
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [url, connect]);

  const send = useCallback((data: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data);
    }
  }, []);

  return { messages, isConnected, send };
}
