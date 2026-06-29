import { NextResponse, type NextRequest } from "next/server";

export async function GET(request: NextRequest) {
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.searchParams.set("error", "SAML 尚未启用（占位）");
  return NextResponse.redirect(url);
}

