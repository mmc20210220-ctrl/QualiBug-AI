const TOKEN_KEYS = new Set(["access_token", "id_token", "refresh_token", "token", "secret", "password", "cookie", "session"]);

export function maskEmail(email: string): string {
  const trimmed = email.trim();
  const at = trimmed.indexOf("@");
  if (at <= 1) return "***" + trimmed.slice(at);
  return trimmed.slice(0, 1) + "***" + trimmed.slice(at - 1);
}

export function maskId(value: string, visiblePrefix = 4, visibleSuffix = 4): string {
  const trimmed = value.trim();
  if (trimmed.length <= visiblePrefix + visibleSuffix + 3) return "***";
  return `${trimmed.slice(0, visiblePrefix)}***${trimmed.slice(-visibleSuffix)}`;
}

export function redactUnknown(value: unknown): unknown {
  if (typeof value === "string") {
    if (value.length > 80) return maskId(value, 6, 6);
    return value;
  }
  if (Array.isArray(value)) return value.map(redactUnknown);
  if (!value || typeof value !== "object") return value;
  const obj = value as Record<string, unknown>;
  const result: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (TOKEN_KEYS.has(k.toLowerCase())) {
      result[k] = "***";
      continue;
    }
    result[k] = redactUnknown(v);
  }
  return result;
}

