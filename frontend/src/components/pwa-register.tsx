"use client";

import { useEffect } from "react";

export function PWARegister() {
  useEffect(() => {
    if ("serviceWorker" in navigator && process.env.NODE_ENV === "production") {
      navigator.serviceWorker
        .register("/service-worker.js")
        .catch((err) => console.warn("SW registration failed:", err));
    }
  }, []);

  return null;
}
