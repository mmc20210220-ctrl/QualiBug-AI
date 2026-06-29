import { NextResponse, type NextRequest } from "next/server";
import { readAuthConfig } from "@/lib/auth/config";
import { buildOidcAuthorizationRequest } from "@/lib/auth/oidc";

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

  try {
    const redirectUri = `${request.nextUrl.origin}/auth/callback`;
    const auth = await buildOidcAuthorizationRequest(config, redirectUri);

    const response = NextResponse.redirect(auth.url);
    const cookieOptions = {
      httpOnly: true,
      secure: request.nextUrl.protocol === "https:",
      sameSite: "lax" as const,
      path: "/",
      maxAge: 10 * 60,
    };

    const nextParam = request.nextUrl.searchParams.get("next");
    if (nextParam && nextParam.startsWith("/")) {
      response.cookies.set(NEXT_COOKIE, nextParam, cookieOptions);
    }
    response.cookies.set(STATE_COOKIE, auth.state, cookieOptions);
    response.cookies.set(VERIFIER_COOKIE, auth.codeVerifier, cookieOptions);
    return response;
  } catch (error) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("error", error instanceof Error ? error.message : "OIDC 登录初始化失败");
    return NextResponse.redirect(url);
  }
}

