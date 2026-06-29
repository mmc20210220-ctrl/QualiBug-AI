import { NextResponse, type NextRequest } from "next/server";
import { readAuthConfig } from "@/lib/auth/config";
import { SESSION_COOKIE_NAME, verifySessionCookieValue } from "@/lib/auth/session";

export async function GET(request: NextRequest) {
  const config = readAuthConfig();
  const response = NextResponse.redirect(new URL("/login", request.nextUrl.origin));
  response.cookies.delete(SESSION_COOKIE_NAME);

  if (config.mode !== "oidc" || !config.sessionSecret) return response;

  const sessionCookie = request.cookies.get(SESSION_COOKIE_NAME)?.value;
  const session = await verifySessionCookieValue(sessionCookie, config.sessionSecret);
  const endSessionEndpoint = request.cookies.get("qb.oidc.end_session")?.value;

  if (!endSessionEndpoint || !session?.idToken) return response;

  const url = new URL(endSessionEndpoint);
  url.searchParams.set("id_token_hint", session.idToken);
  url.searchParams.set("post_logout_redirect_uri", `${request.nextUrl.origin}/login`);
  const endSessionResponse = NextResponse.redirect(url);
  endSessionResponse.cookies.delete(SESSION_COOKIE_NAME);
  return endSessionResponse;
}
