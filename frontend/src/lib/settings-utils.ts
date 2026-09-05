/**
 * Settings utility functions and types.
 * Extracted from Settings.tsx for better maintainability.
 */
import type { ConnectorRecord } from '../api/client';
import type { DbOption } from '../components/settings/DbCredentialPanel';

// ─── Types ───────────────────────────────────────────────────────────────────

export type TenantCreateResponse = {
  ok?: boolean;
  error?: string;
  tenant_id?: string;
  username?: string;
};

export type RoleAccount = {
  role: string;
  username: string;
  password: string;
};

export type SavedServiceConfig = {
  name?: string;
  base_url?: string;
  enabled?: boolean;
  auth?: Record<string, unknown>;
  db?: Record<string, unknown>;
  admin_user?: string;
  admin_pass?: string;
  bearer_token?: string;
  api_key?: string;
  login_api?: string;
  auth_type?: 'password_login' | 'bearer_token' | 'api_key';
};

/**
 * Build the environment topology from the same service configuration that the
 * run center consumes. The pilot connector registry remains a compatibility
 * projection, so it may be absent even when a project has usable services.
 */
export function serviceConfigAsConnector(
  config: SavedServiceConfig,
  legacy?: ConnectorRecord,
): ConnectorRecord {
  const name = asString(config.name).trim();
  const endpoint = asString(config.base_url).trim();
  const identity = normalizeKey(name || endpoint) || 'unnamed';
  return {
    connector_id: legacy?.connector_id || `service_config:${identity}`,
    kind: legacy?.kind || 'http_api',
    display_name: name || legacy?.display_name || endpoint || '未命名服务',
    enabled: config.enabled !== false,
    system_name: legacy?.system_name || name || undefined,
    module_name: legacy?.module_name || undefined,
    endpoint_ref: endpoint || legacy?.endpoint_ref || undefined,
    credential_ref: legacy?.credential_ref || undefined,
    external_ref: legacy?.external_ref,
    created_at_utc: legacy?.created_at_utc,
    last_sync_at_utc: legacy?.last_sync_at_utc,
    last_sync_status: legacy?.last_sync_status,
  };
}

function connectorMatchesServiceConfig(connector: ConnectorRecord, config: SavedServiceConfig): boolean {
  const name = normalizeKey(asString(config.name));
  const endpoint = normalizeKey(asString(config.base_url));
  const connectorNames = [connector.display_name, connector.system_name]
    .map((value) => normalizeKey(value || ''))
    .filter(Boolean);
  return Boolean(
    (name && connectorNames.includes(name))
    || (endpoint && normalizeKey(connector.endpoint_ref || '') === endpoint),
  );
}

/**
 * Merge legacy connector metadata into canonical service rows without
 * duplicating the same target in the settings tree. Registry-only rows remain
 * visible for backward compatibility, but service configs own the identity and
 * enabled state whenever both projections exist.
 */
export function buildSettingsTopologyConnectors(
  services: SavedServiceConfig[],
  legacyConnectors: ConnectorRecord[],
): ConnectorRecord[] {
  const remaining = [...legacyConnectors];
  const canonical = services.map((service) => {
    const index = remaining.findIndex((connector) => connectorMatchesServiceConfig(connector, service));
    const legacy = index >= 0 ? remaining.splice(index, 1)[0] : undefined;
    return serviceConfigAsConnector(service, legacy);
  });
  return canonical.concat(remaining);
}

/**
 * Re-submit a masked service row while changing only its enabled state. The
 * backend preserves masked credentials, so toggling a service cannot erase
 * the customer's existing auth or database material.
 */
export function serviceConfigUpdatePayload(
  config: SavedServiceConfig,
  patch: { enabled?: boolean } = {},
): Record<string, unknown> {
  const db = extractDbConfig(config);
  const roleAccounts = extractRoleAccounts(config)
    .filter((account) => account.role && (account.username || account.password));
  return {
    name: asString(config.name).trim(),
    base_url: asString(config.base_url).trim(),
    enabled: patch.enabled ?? config.enabled !== false,
    login_api: extractLoginApi(config),
    auth_type: extractAuthType(config),
    role_accounts: roleAccounts,
    bearer_token: extractBearerToken(config),
    api_key: extractApiKey(config),
    db_host: db.host,
    db_port: db.port || getDbDefaultPort(db.type),
    db_name: db.name,
    db_user: db.user,
    db_pass: db.password,
  };
}

// ─── Constants ───────────────────────────────────────────────────────────────

export const DB_OPTIONS: DbOption[] = [
  { v: 'postgresql', l: 'PostgreSQL', p: 5432, c: 'relational' },
  { v: 'mysql', l: 'MySQL', p: 3306, c: 'relational' },
  { v: 'mariadb', l: 'MariaDB', p: 3306, c: 'relational' },
  { v: 'sqlserver', l: 'SQL Server', p: 1433, c: 'relational' },
  { v: 'oracle', l: 'Oracle', p: 1521, c: 'relational' },
  { v: 'db2', l: 'IBM DB2', p: 50000, c: 'relational' },
  { v: 'mongodb', l: 'MongoDB', p: 27017, c: 'nosql' },
  { v: 'redis', l: 'Redis', p: 6379, c: 'nosql' },
  { v: 'elasticsearch', l: 'Elasticsearch', p: 9200, c: 'nosql' },
  { v: 'cassandra', l: 'Cassandra', p: 9042, c: 'nosql' },
  { v: 'neo4j', l: 'Neo4j', p: 7687, c: 'nosql' },
  { v: 'clickhouse', l: 'ClickHouse', p: 8123, c: 'nosql' },
];

const AUTH_METADATA_KEYS = new Set(['type', 'auth_type', 'login_api', 'bearer_token', 'api_key']);

// ─── Generic helpers ─────────────────────────────────────────────────────────

export function getErrorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export function buildTenantId(name: string): string {
  return name.replace(/[^a-zA-Z0-9\u4e00-\u9fff]/g, '_').toLowerCase().slice(0, 32) || `client_${Date.now()}`;
}

export function getDbDefaultPort(type: string): string {
  return String(DB_OPTIONS.find((item) => item.v === type)?.p || '');
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

export function asString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

export function normalizeKey(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]+/g, '-').replace(/^-+|-+$/g, '');
}

// ─── Role account helpers ────────────────────────────────────────────────────

export function normalizeRoleAccounts(accounts: RoleAccount[]): RoleAccount[] {
  const cleaned = accounts
    .map((account) => ({
      role: account.role.trim(),
      username: account.username.trim(),
      password: account.password,
    }))
    .filter((account) => account.role || account.username || account.password);

  // Industry-agnostic: preserve the customer-declared role order. Do not force any
  // specific role (e.g. admin) to the top; role names vary across industries.
  return cleaned;
}

export function extractRoleAccounts(config?: SavedServiceConfig | null): RoleAccount[] {
  const auth = asRecord(config?.auth);
  const accounts = Object.entries(auth)
    .filter(([key, value]) => !AUTH_METADATA_KEYS.has(key) && value && typeof value === 'object')
    .map(([role, value]) => {
      const entry = asRecord(value);
      return {
        role,
        username: asString(entry.username),
        password: asString(entry.password),
      };
    })
    .filter((account) => account.role && (account.username || account.password));

  if (!accounts.length && (config?.admin_user || config?.admin_pass)) {
    accounts.push({
      role: 'admin',
      username: asString(config.admin_user),
      password: asString(config.admin_pass),
    });
  }

  const normalized = normalizeRoleAccounts(accounts);
  return normalized.length ? normalized : [{ role: '', username: '', password: '' }];
}

// ─── Auth extraction helpers ─────────────────────────────────────────────────

export function extractAuthType(config?: SavedServiceConfig | null): 'password_login' | 'bearer_token' | 'api_key' {
  const auth = asRecord(config?.auth);
  const authType = asString(auth.type || auth.auth_type || config?.auth_type);
  if (authType === 'bearer_token' || authType === 'api_key') return authType;
  return 'password_login';
}

export function extractLoginApi(config?: SavedServiceConfig | null): string {
  const auth = asRecord(config?.auth);
  return asString(auth.login_api || config?.login_api) || '/auth/login';
}

export function extractBearerToken(config?: SavedServiceConfig | null): string {
  const auth = asRecord(config?.auth);
  return asString(auth.bearer_token || config?.bearer_token);
}

export function extractApiKey(config?: SavedServiceConfig | null): string {
  const auth = asRecord(config?.auth);
  return asString(auth.api_key || config?.api_key);
}

// ─── DB config helpers ───────────────────────────────────────────────────────

export function extractDbConfig(config?: SavedServiceConfig | null) {
  const db = asRecord(config?.db);
  return {
    type: asString(db.type) || 'postgresql',
    host: asString(db.host),
    port: asString(db.port),
    name: asString(db.name),
    user: asString(db.user),
    password: asString(db.password),
  };
}

// ─── Config detection helpers ────────────────────────────────────────────────

export function hasConfiguredAuthMaterial(config?: SavedServiceConfig | null): boolean {
  const auth = asRecord(config?.auth);
  if (asString(auth.bearer_token) || asString(auth.api_key)) return true;
  return Object.entries(auth).some(([key, value]) => {
    if (AUTH_METADATA_KEYS.has(key)) return false;
    const entry = asRecord(value);
    return Boolean(asString(entry.username) || asString(entry.password));
  });
}

export function hasConfiguredDbMaterial(config?: SavedServiceConfig | null): boolean {
  const db = asRecord(config?.db);
  return Boolean(asString(db.host) && asString(db.name));
}

// ─── Service matching ────────────────────────────────────────────────────────

export function findMatchingServiceConfig(connector: ConnectorRecord, services: SavedServiceConfig[]): SavedServiceConfig | null {
  const endpoint = normalizeKey(connector.endpoint_ref || '');
  const displayName = normalizeKey(connector.display_name || '');
  const systemName = normalizeKey(connector.system_name || '');
  const moduleName = normalizeKey(connector.module_name || '');

  return services.find((service) => normalizeKey(asString(service.base_url)) === endpoint)
    || services.find((service) => normalizeKey(asString(service.name)) === displayName)
    || services.find((service) => normalizeKey(asString(service.name)) === `${systemName}-${moduleName}`)
    || services.find((service) => normalizeKey(asString(service.name)) === systemName)
    || null;
}

// ─── Shared onboarding extractors（单一进度口径的数据提取 SSOT）──────────────

/** 从 /v1/services/credentials 响应中提取服务配置列表。 */
export function extractServiceConfigs(payload: unknown): SavedServiceConfig[] {
  const root = asRecord(payload);
  return Array.isArray(root.services)
    ? root.services.map((item) => asRecord(item) as SavedServiceConfig)
    : [];
}

export type MaterialCounts = {
  materialCount: number;
  onlineMaterialCount: number;
  uploadedMaterialCount: number;
};

/** 从 knowledge asset 响应中统计真实可读资料（deleted 不计入）。 */
export function extractMaterialCounts(payload: unknown): MaterialCounts {
  const root = asRecord(payload);
  const asset = asRecord(root.knowledge_asset || root.data || root);
  const inventory = Array.isArray(asset.sources)
    ? asset.sources
    : Array.isArray(asset.source_inventory)
      ? asset.source_inventory
      : [];
  const sources = inventory
    .map(asRecord)
    .filter((source) => String(source.status || 'active').toLowerCase() !== 'deleted');
  const activeSources = sources.filter((source) => String(source.status || 'active').toLowerCase() === 'active');
  const onlineMaterialCount = activeSources.filter(isOnlineMaterialSource).length;

  return {
    materialCount: activeSources.length,
    onlineMaterialCount,
    uploadedMaterialCount: Math.max(0, activeSources.length - onlineMaterialCount),
  };
}

/** 在线资料判定：后端 source_origin 或 connector 引用，前端不设白名单。 */
export function isOnlineMaterialSource(source: Record<string, unknown>): boolean {
  return String(source.source_origin || '').toUpperCase() === 'ONLINE_CONNECTOR'
    || String(source.source_ref || '').startsWith('connector://');
}
