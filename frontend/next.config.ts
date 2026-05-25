import type { NextConfig } from "next";

const API_PROXY_TARGET =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const ALLOWED_DEV_ORIGINS = (process.env.MEGOOCI_ALLOWED_DEV_ORIGINS ?? "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ALLOWED_DEV_ORIGINS,
  // Increase the proxy timeout for API rewrites. LLM reasoning models
  // can take 60+ seconds; the default 30s is too aggressive. SSE streaming
  // keeps the connection alive, but this is a safety-net for non-streamed
  // endpoints and slow providers.
  experimental: {
    proxyTimeout: 120_000,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_PROXY_TARGET}/api/:path*`,
      },
      {
        source: "/ws/:path*",
        destination: `${API_PROXY_TARGET}/ws/:path*`,
      },
    ];
  },
};

export default nextConfig;
