"use client";

/**
 * useBuildUpdates — subscribes to the /ws/builds/updates WebSocket channel
 * and calls an `onUpdate` callback for every `build_update` event received.
 *
 * Unlike the generic useWebSocket hook it parses JSON in-place, without
 * accumulating a growing message array.
 */

import { useEffect, useRef } from "react";
import { useAuthStore } from "@/lib/auth";
import { openLiveSocket } from "@/lib/live-socket";
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
  // Signed in or not, rather than the token: the socket fetches the current
  // token itself, and a renewed one is no reason to reconnect.
  const signedIn = useAuthStore((s) => !!s.accessToken);
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;

  useEffect(() => {
    if (!signedIn) return;

    return openLiveSocket("/api/v1/ws/builds/updates", {
      onMessage: (text) => {
        try {
          const data = JSON.parse(text);
          if (data.event === "build_update") {
            onUpdateRef.current(data as BuildUpdatePayload);
          }
        } catch {
          // ignore malformed frames
        }
      },
    });
  }, [signedIn]);
}
