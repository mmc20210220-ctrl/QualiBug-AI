export type AuthMode = "demo" | "oidc";

export interface AuthConfig {
  mode: AuthMode;
  sessionSecret: string;
  oidcIssuer?: string;
  oidcClientId?: string;
  oidcClientSecret?: string;
  oidcScopes: string;
}

export function readAuthConfig(): AuthConfig {
  const mode = (process.env.AUTH_MODE ?? "demo") as AuthMode;
  return {
    mode,
    sessionSecret: process.env.AUTH_SESSION_SECRET ?? "",
    oidcIssuer: process.env.OIDC_ISSUER,
    oidcClientId: process.env.OIDC_CLIENT_ID,
    oidcClientSecret: process.env.OIDC_CLIENT_SECRET,
    oidcScopes: process.env.OIDC_SCOPES ?? "openid profile email",
  };
}

