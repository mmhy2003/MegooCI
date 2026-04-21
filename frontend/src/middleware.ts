import { NextResponse, type NextRequest } from "next/server";

const PUBLIC_PATHS = new Set([
  "/login",
  "/signup",
  "/invite/accept",
  "/forgot-password",
  "/reset-password",
]);

function isPublicPath(pathname: string): boolean {
  if (PUBLIC_PATHS.has(pathname)) return true;
  if (pathname.startsWith("/_next/") || pathname.startsWith("/api/")) return true;
  if (pathname === "/favicon.ico" || pathname.startsWith("/icons/")) return true;
  return false;
}

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")));
    return payload as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (isPublicPath(pathname)) {
    return NextResponse.next();
  }

  const token =
    request.cookies.get("megooci_access_token")?.value ??
    null;

  let hasValidToken = false;
  if (token) {
    const payload = decodeJwtPayload(token);
    if (payload && typeof payload.exp === "number") {
      hasValidToken = payload.exp * 1000 > Date.now();
    }
  }

  if (!hasValidToken) {
    const accessToken = request.headers.get("x-access-token");
    if (accessToken) {
      const payload = decodeJwtPayload(accessToken);
      if (payload && typeof payload.exp === "number") {
        hasValidToken = payload.exp * 1000 > Date.now();
      }
    }
  }

  if (!hasValidToken) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("redirect", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|icons/).*)",
  ],
};
