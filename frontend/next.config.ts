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
