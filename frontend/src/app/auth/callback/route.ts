import { NextResponse, type NextRequest } from "next/server";
import { readAuthConfig } from "@/lib/auth/config";
import { createSessionCookieValue, SESSION_COOKIE_NAME } from "@/lib/auth/session";
import { actorFromIdTokenClaims, exchangeAuthorizationCode } from "@/lib/auth/oidc";

const STATE_COOKIE = "qb.oidc.state";
const VERIFIER_COOKIE = "qb.oidc.verifier";
const NEXT_COOKIE = "qb.oidc.next";

export async function GET(request: NextRequest) {
  const config = readAuthConfig();
  if (config.mode !== "oidc") {
    const url = request.nextUrl.clone();
    url.pathname = "/projects";
    url.search = "";
    return NextResponse.redirect(url);
  }

  const state = request.nextUrl.searchParams.get("state") ?? "";
  const code = request.nextUrl.searchParams.get("code") ?? "";
  const storedState = request.cookies.get(STATE_COOKIE)?.value ?? "";
  const codeVerifier = request.cookies.get(VERIFIER_COOKIE)?.value ?? "";
  const next = request.cookies.get(NEXT_COOKIE)?.value ?? "/projects";

  if (!state || !code || !storedState || state !== storedState || !codeVerifier) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("error", "OIDC 回调校验失败");
    return NextResponse.redirect(url);
  }

  if (!config.sessionSecret) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("error", "缺少 AUTH_SESSION_SECRET，无法建立会话");
    return NextResponse.redirect(url);
  }

  try {
    const redirectUri = `${request.nextUrl.origin}/auth/callback`;
    const { tokens, claims, exp, issuer, endSessionEndpoint } = await exchangeAuthorizationCode({
      config,
      redirectUri,
      code,
      codeVerifier,
    });

    const actor = actorFromIdTokenClaims(claims, config.oidcClientId);
    const cookieValue = await createSessionCookieValue(
      {
        actor,
        exp,
        accessToken: tokens.access_token,
        idToken: tokens.id_token,
        issuer,
      },
      config.sessionSecret,
    );

    const response = NextResponse.redirect(new URL(next.startsWith("/") ? next : "/projects", request.nextUrl.origin));
    response.cookies.set(SESSION_COOKIE_NAME, cookieValue, {
      httpOnly: true,
      secure: request.nextUrl.protocol === "https:",
      sameSite: "lax",
      path: "/",
      maxAge: Math.max(60, exp - Math.floor(Date.now() / 1000)),
    });
    response.cookies.delete(STATE_COOKIE);
    response.cookies.delete(VERIFIER_COOKIE);
    response.cookies.delete(NEXT_COOKIE);
    if (endSessionEndpoint) {
      response.cookies.set("qb.oidc.end_session", endSessionEndpoint, {
        httpOnly: true,
        secure: request.nextUrl.protocol === "https:",
        sameSite: "lax",
        path: "/",
        maxAge: 24 * 60 * 60,
      });
    }
    return response;
  } catch (error) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("error", error instanceof Error ? error.message : "OIDC 回调处理失败");
    return NextResponse.redirect(url);
  }
}

