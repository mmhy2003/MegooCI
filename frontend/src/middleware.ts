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
    const payload = JSON.parse(
      atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"))
    );
    return payload as Record<string, unknown>;
  } catch {
    return null;
  }
}

function hasValidCookieToken(request: NextRequest): boolean {
  const token = request.cookies.get("megooci_access_token")?.value;
  if (!token) return false;
  const payload = decodeJwtPayload(token);
  if (!payload || typeof payload.exp !== "number") return false;
  return payload.exp * 1000 > Date.now();
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (isPublicPath(pathname)) {
    return NextResponse.next();
  }

  if (hasValidCookieToken(request)) {
    return NextResponse.next();
  }

  // No valid cookie token found. Since the app stores tokens in
  // localStorage and syncs them to cookies on the client, the cookie
  // may simply not exist yet (e.g. first navigation after login or a
  // returning user whose cookie expired while the localStorage token
  // was refreshed client-side). In that case we let the page load so
  // client-side hydration can set the cookie. The `AppLayout` component
  // handles the client-side redirect to `/login` when there truly is
  // no token.
  //
  // However for `/admin/*` routes we add an extra guard: redirect to
  // login so the admin UI never even begins to render for anonymous
  // visitors hitting the URL directly.  Regular authenticated users
  // who simply lack admin rights will be handled client-side by
  // `<RequireAdmin>`.
  if (pathname.startsWith("/admin")) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("redirect", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icons/).*)"],
};
