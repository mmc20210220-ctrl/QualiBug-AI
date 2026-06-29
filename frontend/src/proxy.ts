import { NextResponse, type NextRequest } from "next/server";
import { canAccessProject, canSeeProjectsIndex } from "@/lib/auth/authz";
import { readAuthConfig } from "@/lib/auth/config";
import { SESSION_COOKIE_NAME, verifySessionCookieValue } from "@/lib/auth/session";

function isPublicPath(pathname: string): boolean {
  if (pathname === "/login" || pathname === "/no-access") return true;
  if (pathname.startsWith("/auth/")) return true;
  if (pathname.startsWith("/_next/")) return true;
  if (pathname === "/favicon.ico") return true;
  return false;
}

function buildLoginRedirect(request: NextRequest): NextResponse {
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.searchParams.set("next", request.nextUrl.pathname + request.nextUrl.search);
  return NextResponse.redirect(url);
}

export async function proxy(request: NextRequest) {
  const config = readAuthConfig();
  if (config.mode === "demo") return NextResponse.next();
  if (isPublicPath(request.nextUrl.pathname)) return NextResponse.next();

  const shouldGuard = request.nextUrl.pathname === "/projects" || request.nextUrl.pathname.startsWith("/projects/");
  if (!shouldGuard) return NextResponse.next();

  if (!config.sessionSecret) return buildLoginRedirect(request);
  const cookieValue = request.cookies.get(SESSION_COOKIE_NAME)?.value;
  const session = await verifySessionCookieValue(cookieValue, config.sessionSecret);
  if (!session) return buildLoginRedirect(request);

  if (request.nextUrl.pathname === "/projects" && !canSeeProjectsIndex(session.actor)) {
    const url = request.nextUrl.clone();
    url.pathname = "/no-access";
    url.search = "";
    return NextResponse.redirect(url);
  }

  const match = request.nextUrl.pathname.match(/^\/projects\/([^/]+)/);
  if (match) {
    const projectId = decodeURIComponent(match[1]);
    if (!canAccessProject(session.actor, projectId)) {
      const url = request.nextUrl.clone();
      url.pathname = "/no-access";
      url.search = "";
      return NextResponse.redirect(url);
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api/).*)"],
};

