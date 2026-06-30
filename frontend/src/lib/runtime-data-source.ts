export type RequestedDataMode = "auto" | "demo" | "real";
export type ResolvedDataMode = "demo" | "real";

export interface DataSourceConfig {
  requestedMode: RequestedDataMode;
  resolvedMode: ResolvedDataMode;
  apiBaseUrl?: string;
  hasExplicitApiBaseUrl: boolean;
}

function parseRequestedDataMode(value: string | undefined): RequestedDataMode {
  const normalized = value?.trim().toLowerCase();
  if (normalized === "demo" || normalized === "real") return normalized;
  return "auto";
}

export function readDataSourceConfig(): DataSourceConfig {
  const requestedMode = parseRequestedDataMode(process.env.QUALIBUG_DATA_MODE ?? process.env.NEXT_PUBLIC_QUALIBUG_DATA_MODE);
  const apiBaseUrl = process.env.QUALIBUG_API_BASE_URL ?? process.env.NEXT_PUBLIC_QUALIBUG_API_BASE_URL;
  const hasExplicitApiBaseUrl = typeof apiBaseUrl === "string" && apiBaseUrl.trim().length > 0;

  let resolvedMode: ResolvedDataMode;
  if (requestedMode === "demo") resolvedMode = "demo";
  else if (requestedMode === "real") resolvedMode = "real";
  else resolvedMode = hasExplicitApiBaseUrl ? "real" : "demo";

  return {
    requestedMode,
    resolvedMode,
    apiBaseUrl: hasExplicitApiBaseUrl ? apiBaseUrl : undefined,
    hasExplicitApiBaseUrl,
  };
}

export function shouldUseDemoData(): boolean {
  return readDataSourceConfig().resolvedMode === "demo";
}
