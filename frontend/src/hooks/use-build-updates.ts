"use client";

/**
 * useBuildUpdates — subscribes to the /ws/builds/updates WebSocket channel
 * and calls an `onUpdate` callback for every `build_update` event received.
 *
 * The hook manages its own WebSocket lifecycle (connect / reconnect / cleanup)
 * independently of the generic useWebSocket hook so it can parse JSON in-place
 * without accumulating a growing message array.
 */

import { useEffect, useRef, useCallback } from "react";
import { useAuthStore } from "@/lib/auth";
import type { Build } from "@/lib/api";

export type BuildUpdatePayload = Pick<
  Build,
  | "id"
  | "pipeline_id"
  | "number"
  | "branch"
  | "commit_sha"
  | "status"
  | "trigger_type"
  | "started_at"
  | "finished_at"
  | "created_at"
  | "updated_at"
  | "triggered_by"
>;

export function useBuildUpdates(onUpdate: (build: BuildUpdatePayload) => void) {
  const { accessToken } = useAuthStore();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;

  const connect = useCallback(
    (token: string) => {
      if (typeof window === "undefined") return;

      const apiBase = process.env.NEXT_PUBLIC_API_URL || "";
      let wsUrl: string;
      if (apiBase) {
        const url = new URL(apiBase);
        const wsProto = url.protocol === "https:" ? "wss:" : "ws:";
        wsUrl = `${wsProto}//${url.host}/api/v1/ws/builds/updates?token=${encodeURIComponent(token)}`;
      } else {
        const wsProto =
          window.location.protocol === "https:" ? "wss:" : "ws:";
        wsUrl = `${wsProto}//${window.location.host}/api/v1/ws/builds/updates?token=${encodeURIComponent(token)}`;
      }

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data as string);
          if (data.event === "build_update") {
            onUpdateRef.current(data as BuildUpdatePayload);
          }
        } catch {
          // ignore malformed frames
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        // Reconnect after 3 s — mirrors the generic useWebSocket hook.
        reconnectTimerRef.current = setTimeout(() => {
          if (token) connect(token);
        }, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };
    },
    [],
  );

  useEffect(() => {
    if (!accessToken) return;

    connect(accessToken);

    return () => {
      clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [accessToken, connect]);
}
