import { normalizeRole } from "@/lib/auth/authz";
import type { AuthConfig } from "@/lib/auth/config";
import type { SessionActor } from "@/lib/auth/session";

export interface OidcDiscovery {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
}

let cachedDiscovery: { issuer: string; value: OidcDiscovery; fetchedAtMs: number } | null = null;

function base64UrlEncode(bytes: Uint8Array): string {
  return Buffer.from(bytes)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function randomBase64Url(size: number): string {
  const bytes = new Uint8Array(size);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

async function sha256Base64Url(input: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(input));
  return base64UrlEncode(new Uint8Array(digest));
}

export async function loadOidcDiscovery(config: AuthConfig): Promise<OidcDiscovery> {
  if (!config.oidcIssuer) throw new Error("OIDC issuer 未配置（OIDC_ISSUER）");
  const now = Date.now();
  if (cachedDiscovery && cachedDiscovery.issuer === config.oidcIssuer && now - cachedDiscovery.fetchedAtMs < 5 * 60_000) {
    return cachedDiscovery.value;
  }
  const url = `${config.oidcIssuer.replace(/\/$/, "")}/.well-known/openid-configuration`;
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`无法读取 OIDC discovery: ${response.status}`);
  const payload = (await response.json()) as Partial<OidcDiscovery>;
  if (!payload.issuer || !payload.authorization_endpoint || !payload.token_endpoint) {
    throw new Error("OIDC discovery 返回缺少必填字段");
  }
  const value: OidcDiscovery = {
    issuer: payload.issuer,
    authorization_endpoint: payload.authorization_endpoint,
    token_endpoint: payload.token_endpoint,
    end_session_endpoint: payload.end_session_endpoint,
  };
  cachedDiscovery = { issuer: config.oidcIssuer, value, fetchedAtMs: now };
  return value;
}

export interface OidcAuthRequest {
  url: string;
  state: string;
  codeVerifier: string;
}

export async function buildOidcAuthorizationRequest(config: AuthConfig, redirectUri: string): Promise<OidcAuthRequest> {
  if (!config.oidcClientId) throw new Error("OIDC client_id 未配置（OIDC_CLIENT_ID）");
  const discovery = await loadOidcDiscovery(config);
  const state = randomBase64Url(24);
  const codeVerifier = randomBase64Url(48);
  const codeChallenge = await sha256Base64Url(codeVerifier);
  const url = new URL(discovery.authorization_endpoint);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", config.oidcClientId);
  url.searchParams.set("redirect_uri", redirectUri);
  url.searchParams.set("scope", config.oidcScopes);
  url.searchParams.set("state", state);
  url.searchParams.set("code_challenge", codeChallenge);
  url.searchParams.set("code_challenge_method", "S256");
  return { url: url.toString(), state, codeVerifier };
}

export interface OidcTokenResponse {
  access_token?: string;
  id_token?: string;
  token_type?: string;
  expires_in?: number;
}

function decodeJwtPayload(token: string): Record<string, unknown> {
  const parts = token.split(".");
  if (parts.length < 2) return {};
  const payload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  const padded = payload + "=".repeat((4 - (payload.length % 4)) % 4);
  const json = Buffer.from(padded, "base64").toString("utf8");
  try {
    const parsed = JSON.parse(json) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

function stringArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((v) => String(v)).filter(Boolean);
  if (typeof value === "string" && value.trim()) return [value.trim()];
  return [];
}

function readNestedRoles(claims: Record<string, unknown>, clientId?: string): string[] {
  const realmAccess = claims.realm_access as Record<string, unknown> | undefined;
  const realmRoles = realmAccess ? stringArray(realmAccess.roles) : [];
  if (!clientId) return realmRoles;
  const resourceAccess = claims.resource_access as Record<string, unknown> | undefined;
  const clientAccess = resourceAccess ? (resourceAccess[clientId] as Record<string, unknown> | undefined) : undefined;
  const clientRoles = clientAccess ? stringArray(clientAccess.roles) : [];
  return [...realmRoles, ...clientRoles];
}

export function actorFromIdTokenClaims(claims: Record<string, unknown>, clientId?: string): SessionActor {
  const rawRoles = [...stringArray(claims.roles), ...stringArray(claims.role), ...readNestedRoles(claims, clientId)];
  const roles = rawRoles.map(normalizeRole).filter((r): r is NonNullable<typeof r> => Boolean(r));
  const projectIds = stringArray(claims.projects ?? claims.project_ids ?? claims.project_scopes ?? claims.projectScopes);
  const userId = String(claims.sub ?? claims.user_id ?? claims.uid ?? "");
  return {
    userId: userId || "unknown",
    email: typeof claims.email === "string" ? claims.email : undefined,
    name: typeof claims.name === "string" ? claims.name : typeof claims.preferred_username === "string" ? claims.preferred_username : undefined,
    tenantId: typeof claims.tenant_id === "string" ? claims.tenant_id : typeof claims.tid === "string" ? claims.tid : undefined,
    roles: roles.length ? roles : ["project_viewer"],
    projectIds,
  };
}

export async function exchangeAuthorizationCode(options: {
  config: AuthConfig;
  redirectUri: string;
  code: string;
  codeVerifier: string;
}): Promise<{ tokens: OidcTokenResponse; claims: Record<string, unknown>; exp: number; issuer: string; endSessionEndpoint?: string }> {
  const { config, redirectUri, code, codeVerifier } = options;
  if (!config.oidcClientId) throw new Error("OIDC client_id 未配置（OIDC_CLIENT_ID）");
  const discovery = await loadOidcDiscovery(config);
  const body = new URLSearchParams();
  body.set("grant_type", "authorization_code");
  body.set("code", code);
  body.set("redirect_uri", redirectUri);
  body.set("client_id", config.oidcClientId);
  body.set("code_verifier", codeVerifier);
  const headers: Record<string, string> = { "Content-Type": "application/x-www-form-urlencoded" };
  if (config.oidcClientSecret) {
    const basic = Buffer.from(`${config.oidcClientId}:${config.oidcClientSecret}`).toString("base64");
    headers.Authorization = `Basic ${basic}`;
  }
  const response = await fetch(discovery.token_endpoint, { method: "POST", headers, body, cache: "no-store" });
  if (!response.ok) throw new Error(`OIDC token exchange 失败: ${response.status}`);
  const tokens = (await response.json()) as OidcTokenResponse;
  const claims = tokens.id_token ? decodeJwtPayload(tokens.id_token) : {};
  const exp = typeof claims.exp === "number" ? claims.exp : Math.floor(Date.now() / 1000) + Math.max(60, tokens.expires_in ?? 3600);
  return { tokens, claims, exp, issuer: discovery.issuer, endSessionEndpoint: discovery.end_session_endpoint };
}

