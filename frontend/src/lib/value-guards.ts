/**
 * 统一的价值收敛守卫（value guards）。
 *
 * 全仓库唯一来源：任何 "unknown → 安全类型" 的收敛函数都从这里导出，
 * 禁止在页面/组件/api 文件里再定义本地副本。
 *
 * 语义约定：
 * - asText：字符串则 trim，非字符串返回 ''（用于展示文案）
 * - asString：字符串则原样返回（不 trim），非字符串返回 ''（用于结构化值）
 * - asNum：数字或可解析为有限数字则返回数值，否则返回 fallback
 */
export type JsonRecord = Record<string, unknown>;

export function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {};
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

export function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

export function asNum(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** 仅接受真实 number（不做字符串解析），其余返回 undefined。用于"缺失即省略"的可选数值字段。 */
export function asOptionalNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

/** 仅接受真实 number（不做字符串解析），其余返回 0。用于严格协议字段。 */
export function asStrictNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}
