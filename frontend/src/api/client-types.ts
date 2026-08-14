/**
 * client API 的纯类型定义（无运行时依赖）。
 * 拆分自 api/client.ts，供 session.ts 与 client.ts 共享；
 * client.ts 会再导出这些类型，保持对外 import 路径不变。
 */

export type JsonRecord = Record<string, unknown>;

export type LoginResult = {
  ok: boolean;
  token: string;
  tenantId: string;
  username: string;
  role: string;
};

export type SessionResult = {
  authenticated: boolean;
  tenantId: string;
  username: string;
  role: string;
  authType: string;
};

export type RegisterResult = {
  ok: boolean;
  tenantId: string;
  username: string;
  role: string;
};

export type CustomerWorkspace = {
  project_id?: string;
  customer_name?: string;
  project_name?: string;
  system_name?: string;
  industry?: string;
};

export type ConnectorRecord = {
  connector_id: string;
  kind: string;
  display_name: string;
  enabled: boolean;
  system_name?: string;
  module_name?: string;
  endpoint_ref?: string;
  credential_ref?: string;
  external_ref?: string;
  created_at_utc?: string;
  last_sync_at_utc?: string;
  last_sync_status?: string;
};

export type CampaignGovernance = {
  campaign_id?: string;
  campaign_status?: 'active' | 'completed' | 'coverage_deferred' | 'blocked' | string;
  scope_id?: string;
  environment_ref?: string;
  source_id?: string;
  source_hash?: string;
  source_snapshot_hash?: string;
  round_count?: number;
  slice_budget?: number;
  automatic_round_limit?: number;
  attempted_slice_count?: number;
  confirmed_slice_count?: number;
  current_campaign_confirmed_slice_count?: number;
  current_campaign_customer_ready_defect_count?: number;
  current_campaign_bundle_finding_count_raw?: number;
  family_customer_ready_defect_count?: number;
  family_report_real_finding_count?: number;
  family_historical_carryover_defect_count?: number;
  confirmed_shelf_alignment_status?: string;
  confirmed_shelf_reporting_scope?: string;
  coverage_deferred_reason?: string;
  next_campaign_reason?: string;
};

export type TestDataPlan = {
  status?: 'ready' | 'blocked_with_testability_gap' | string;
  strategy?: string;
  missing_requirements?: string[];
  coverage_gaps?: Array<{ kind?: string; code?: string; campaign_id?: string }>;
};

export type V12ScanResult = {
  ok: boolean;
  scan_id?: string;
  grade?: string;
  score?: number;
  coverage?: number;
  total_findings?: number;
  total_ms?: number;
  execution_status?: string;
  layers?: Record<string, { findings: number; ms: number; tool?: string }>;
  spectrum?: { capabilities_run: number; total_findings: number };
  auto_har?: Record<string, unknown>;
  campaign?: CampaignGovernance;
  coverage_gaps?: Array<Record<string, unknown>>;
  runtime_contract?: Record<string, unknown>;
  test_data_plan?: TestDataPlan;
  report_path?: string;
  error?: string;
  message?: string;
};

export type RegressionRunResult = {
  ok: boolean;
  project_id?: string;
  mode?: 'smoke' | 'release' | 'full' | string;
  summary?: {
    generated_at?: string;
    suite_mode?: string;
    suite_mode_label?: string;
    total_probe_count?: number;
    executed_count?: number;
    passed_count?: number;
    failed_count?: number;
    needs_review_count?: number;
    skipped_count?: number;
  };
  ci_feedback?: {
    gate_status?: string;
    ci_message?: string;
    exit_code?: number;
    reopen_issue_ids?: string[];
  };
  regression_summary?: Record<string, unknown>;
  failures?: Array<Record<string, unknown>>;
  artifacts?: Record<string, string>;
  governance?: {
    dry_run?: boolean;
    allow_destructive_execution?: boolean;
    safe_by_default?: boolean;
  };
  error?: string;
  message?: string;
};

export type ScanPreflight = {
  ok: boolean;
  ready: boolean;
  reasons: Array<{ code: string; message: string }>;
};

export interface ProjectMetadata {
  industry?: string;
  module_scope?: string[] | string;
  production_data_exclusion?: string[] | string;
}
