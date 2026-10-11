import { getAccessToken, refreshAccessTokenOnce } from "./api";

const FIRST_RETRY_MS = 3_000;
const MAX_RETRY_MS = 60_000;
// Renew a token this close to its end rather than send it.
const EXPIRY_MARGIN_MS = 30_000;

export interface LiveSocketHandlers {
  onMessage: (data: string) => void;
  onOpen?: () => void;
  onClose?: () => void;
}

/** When a JWT stops being accepted, in ms since the epoch; null if it does not say. */
function expiresAt(token: string): number | null {
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const exp = JSON.parse(atob(payload)).exp;
    return typeof exp === "number" ? exp * 1000 : null;
  } catch {
    return null;
  }
}

function socketUrl(path: string, token: string): string {
  const apiBase = process.env.NEXT_PUBLIC_API_URL || "";
  const base = apiBase ? new URL(apiBase) : window.location;
  const protocol = base.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${base.host}${path}?token=${encodeURIComponent(token)}`;
}

/**
 * Open a WebSocket to the API at *path* and keep it open: when it closes it
 * is opened again, until the returned function is called.
 *
 * Each attempt uses the access token stored at that moment, and renews it
 * first if it has expired. The server refuses an expired token before the
 * socket opens, which looks the same from here as a server that is down, so
 * a tab left open past the token's lifetime would otherwise retry with the
 * same dead token for good.
 *
 * Attempts are spaced further and further apart, up to a minute, and stop
 * once there is no session to connect with.
 */
export function openLiveSocket(
  path: string,
  handlers: LiveSocketHandlers,
): () => void {
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let delay = FIRST_RETRY_MS;
  let stopped = false;

  function retryLater() {
    if (stopped) return;
    timer = setTimeout(connect, delay);
    delay = Math.min(delay * 2, MAX_RETRY_MS);
  }

  async function connect() {
    let token = getAccessToken();
    if (!token) return;

    const expiry = expiresAt(token);
    if (expiry !== null && expiry - Date.now() < EXPIRY_MARGIN_MS) {
      token = await refreshAccessTokenOnce();
      if (stopped) return;
      if (!token) {
        // Refused: the session is gone, and so is the stored token. Anything
        // else (the server could not be reached) is worth another try.
        if (getAccessToken()) retryLater();
        return;
      }
    }

    const ws = new WebSocket(socketUrl(path, token));
    socket = ws;

    ws.onopen = () => {
      delay = FIRST_RETRY_MS;
      handlers.onOpen?.();
    };
    ws.onmessage = (event) => handlers.onMessage(event.data as string);
    ws.onclose = () => {
      socket = null;
      handlers.onClose?.();
      retryLater();
    };
    ws.onerror = () => ws.close();
  }

  connect();

  return () => {
    stopped = true;
    clearTimeout(timer);
    if (socket) {
      // Detach onclose first, otherwise closing here schedules a reconnect.
      socket.onclose = null;
      socket.close();
      socket = null;
    }
  };
}
