export const SESSION_COOKIE_NAME = "qb.session";

export type TenantRole = "tenant_admin" | "project_admin" | "project_viewer" | "auditor";

export interface SessionActor {
  userId: string;
  email?: string;
  name?: string;
  tenantId?: string;
  roles: TenantRole[];
  projectIds: string[];
}

export interface AuthSession {
  actor: SessionActor;
  exp: number;
  accessToken?: string;
  idToken?: string;
  issuer?: string;
}

function base64UrlEncode(bytes: Uint8Array): string {
  const base64 = typeof Buffer !== "undefined" ? Buffer.from(bytes).toString("base64") : btoa(String.fromCharCode(...bytes));
  return base64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64UrlDecodeToBytes(input: string): Uint8Array {
  const base64 = input.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (input.length % 4)) % 4);
  if (typeof Buffer !== "undefined") {
    return new Uint8Array(Buffer.from(base64, "base64"));
  }
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function textEncode(value: string): Uint8Array {
  return new TextEncoder().encode(value);
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

async function importHmacKey(secret: string): Promise<CryptoKey> {
  const digest = await crypto.subtle.digest("SHA-256", toArrayBuffer(textEncode(secret)));
  return crypto.subtle.importKey("raw", digest, { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

async function sign(secret: string, payload: string): Promise<Uint8Array> {
  const key = await importHmacKey(secret);
  const sig = await crypto.subtle.sign("HMAC", key, toArrayBuffer(textEncode(payload)));
  return new Uint8Array(sig);
}

function safeJsonParse(value: string): unknown {
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return null;
  }
}

export async function createSessionCookieValue(session: AuthSession, secret: string): Promise<string> {
  const payload = base64UrlEncode(textEncode(JSON.stringify(session)));
  const sig = base64UrlEncode(await sign(secret, payload));
  return `${payload}.${sig}`;
}

export async function verifySessionCookieValue(value: string | undefined, secret: string): Promise<AuthSession | null> {
  if (!value) return null;
  const [payload, sig] = value.split(".");
  if (!payload || !sig) return null;
  const expected = await sign(secret, payload);
  const actual = base64UrlDecodeToBytes(sig);
  if (expected.length !== actual.length) return null;
  let mismatch = 0;
  for (let i = 0; i < expected.length; i += 1) mismatch |= expected[i] ^ actual[i];
  if (mismatch !== 0) return null;
  const decoded = typeof Buffer !== "undefined" ? Buffer.from(payload.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8") : new TextDecoder().decode(base64UrlDecodeToBytes(payload));
  const parsed = safeJsonParse(decoded);
  if (!parsed || typeof parsed !== "object") return null;
  const maybe = parsed as Partial<AuthSession>;
  if (!maybe.actor || typeof maybe.exp !== "number") return null;
  if (maybe.exp * 1000 <= Date.now()) return null;
  return maybe as AuthSession;
}
