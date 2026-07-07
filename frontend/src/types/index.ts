/**
 * Display-Ready Finding Schema — 后端 display_ready_formatter.py 输出的 1:1 映射。
 * 前端零计算纯渲染，所有字段由后端格式化。
 */

export interface Finding {
  // 基础信息
  id: string;
  title: string;
  severity: 'P0' | 'P1' | 'P2';
  risk_type: string;
  verdict: string;

  // ── 统一 Bug 状态（四态）──
  bug_status: 'reproduced' | 'suspected' | 'risk_clue' | 'not_reproduced';
  bug_status_label: string;
  bug_status_description: string;
  is_reproducible: boolean;
  confidence: number;
  affected_scope: string;
  gate_passed: boolean;
  gate_failures: string[];

  // 分类（后端已解析）
  defect_family: string;
  defect_family_label: string;
  reporting_bucket: string;
  reporting_bucket_label: string;
  quality_assurance_gap: boolean;

  // 证据质量（后端已评分）
  evidence_quality: {
    level: 'validated' | 'partial' | 'suspected' | 'needs_evidence';
    score: number;
    label: string;
    summary: string;
    verified: string[];
    missing: string[];
    next_actions: string[];
    can_reproduce: boolean;
    curl_command: string;
  };

  // 证据链（后端已构建，三视角预过滤）
  evidence_chain: EvidenceStep[];
  evidence_chain_business?: EvidenceStep[];
  evidence_chain_test?: EvidenceStep[];
  evidence_chain_dev?: EvidenceStep[];
  evidence_completeness?: EvidenceCompleteness;

  // ── 五层证据模型 ──
  business_summary: string;
  test_summary: string;
  dev_summary: string;
  expected_actual_comparison: ExpectedActualComparison;
  failed_assertions: FailedAssertion[];
  raw_evidence: RawEvidence;
  technical_details: TechnicalDetails;
  recommended_fix: string;
  regression_suggestions: string[];

  // 复现信息（后端基于 HAR 真实数据生成）
  reproduction: {
    method: string;
    path: string;
    steps: string[];
    curl_command: string;
    is_synthetic?: boolean;
    har_evidence?: {
      status_code: number;
      response_body: string;
      actor: string;
      duration_ms: number;
    };
  };

  // 业务影响（后端已映射中文）
  business_impact: {
    summary: string;
    urgency: string;
    module: string;
  };

  // 排查指引（后端已生成）
  investigation_guidance: {
    primary_area: string;
    relevant_apis: string[];
    relevant_tables: string[];
    log_search: string;
    sql_verify: string;
    trace_id: string;
  };

  // 四层证据状态
  evidence_status: {
    raw_runtime_verdict: string;
    semantic_verdict: string;
    business_evidence_status: string;
    final_review_status: string;
    missing_requirements: string[];
  };

  // 关联文档
  doc_refs: Array<{
    source_id?: string;
    display_name?: string;
    excerpt?: string;
    type?: string;
  }>;

  // 受影响的业务实例（去重合并后的同类缺陷实例列表）
  affected_instances?: string[];
  affected_count?: number;

  // 元数据
  timestamp: string;
  reproducibility_count: number;
  proof: { hash: string; repro_rate: number };

  // 交付轨道（后端单一真相源）
  delivery_track?: 'defect' | 'clue';
  customer_delivery_status?: 'defect' | 'clue';
  customer_delivery_label?: string;
  customer_visible?: boolean;
  customer_delivery_gate_reasons?: string[];
  customer_delivery_gate_explanations?: Array<{
    code?: string;
    label?: string;
    detail?: string;
    next_action?: string;
  }>;
  confirmation_status?: string;
  execution_status?: string;
  execution_block?: string;
  block_reason?: string;
  value_lane?: string;

  // 兼容字段（后端同时返回）
  expected: string;
  actual: string;
  repro_method: string;
  repro_path: string;
  source_entity: string;
  source_value: string;
  evidence_hint: string;
  regression?: {
    included_in_suite: boolean;
    suite_modes: string[];
    latest_status: string;
    latest_status_label: string;
    last_run_at: string;
    last_run_mode: string;
    gate_status: string;
    reason: string;
    regression_probe_id: string;
    issue_id: string;
  };
}

export interface RegressionSummary {
  suite_exists: boolean;
  run_exists: boolean;
  headline: string;
  customer_ready_defect_count: number;
  covered_defect_count: number;
  passed_defect_count: number;
  failed_defect_count: number;
  needs_review_defect_count: number;
  pending_defect_count: number;
  skipped_defect_count: number;
  not_covered_defect_count: number;
  defect_status_counts: Record<string, number>;
  suite: {
    generated_at: string;
    total_probe_count: number;
    smoke_count: number;
    release_count: number;
    full_count: number;
    mode_counts: Record<string, number>;
  };
  latest_run: {
    generated_at: string;
    suite_mode: string;
    suite_mode_label: string;
    gate_status: string;
    ci_message: string;
    total_probe_count: number;
    executed_count: number;
    passed_count: number;
    failed_count: number;
    needs_review_count: number;
    skipped_count: number;
    run_status_counts: Record<string, number>;
    reopen_issue_ids: string[];
  };
}

export interface CommercialAssets {
  status: string;
  finding_count: number;
  customer_ready_reproduction_count: number;
  commercial_handoff: {
    status: string;
    acceptance_status: string;
    safe_for_customer: boolean;
  };
  tracker_sync: {
    payload_status: string;
    payload_gate_status: string;
  };
  delivery_package: {
    status: string;
    package_id: string;
    package_ref: string;
    release_verdict: string;
    evidence_bundle_id: string;
  };
  artifact_refs: Record<string, string>;
}

export interface EvidenceStep {
  tag: 'rule' | 'api' | 'fact' | 'response' | 'db' | 'log' | 'judgment';
  label: string;
  content: string;
  detail: string;
  source?: string;
  confidence?: 'high' | 'medium' | 'low';
  timestamp?: string;
  structured?: Record<string, unknown>;
}

export interface EvidenceCompleteness {
  score: number;
  present_count: number;
  total: number;
  dimensions: Array<{
    key: string;
    label: string;
    present: boolean;
    detail: string;
  }>;
}

// ── 五层证据模型类型 ──

export interface ExpectedActualComparison {
  expected: string;
  actual: string;
  difference: string;
  db_comparison: {
    table: string;
    column: string;
    expected: string;
    actual: string;
    violation: string;
  } | null;
  api_comparison: {
    expected: string;
    actual: string;
    response_body: string;
    duration_ms: number;
  } | null;
}

export interface FailedAssertion {
  type: string;
  label: string;
  expected: string;
  actual: string;
  detail: string;
}

export interface RawEvidence {
  request_raw: {
    method?: string;
    path?: string;
    actor?: string;
    body?: string;
  };
  response_raw: {
    status_code?: number;
    body?: string;
    duration_ms?: number;
  };
  db_snapshot: {
    table?: string;
    column?: string;
    value?: string;
    violation?: string;
    sql_verify?: string;
  };
  logs: {
    trace_id?: string;
    search_hint?: string;
  };
  execution_trace: {
    source_file?: string;
    evidence_hash?: string;
  };
  timestamp: string;
  has_real_evidence: boolean;
}

export interface TechnicalDetails {
  api_endpoint: {
    method: string;
    path: string;
    actor: string;
  };
  response_status: number;
  response_body_excerpt: string;
  related_tables: string[];
  trace_id: string;
  possible_root_cause: string;
  recommended_fix: string;
  regression_suggestions: string[];
  code_module_hint: string;
}

export interface ProjectOverview {
  project_id: string;
  bei_score: number;
  bei_change: number;
  bei_percentile: number;
  bds_score: number;
  bcs_score: number;
  modeled_paths: number;
  pending_paths: number;
  gap_count: number;
  evidence_completeness: number;
  high_priority_findings: number;
  last_scan_minutes_ago: number;
  scan_active: boolean;
}

export interface CoverageData {
  modeled_paths: number;
  executed_probes: number;
  confirmed_findings: number;
  evidence_completeness: number;
}

export interface KnowledgeSource {
  source_id: string;
  filename: string;
  source_type: string;
  status: string;
  size_bytes: number;
  uploaded_at: string;
}

export interface EnvConfig {
  target_environment: string;
  base_url: string;
  request_timeout_seconds: number;
  environments: Array<{
    name: string;
    base_url: string;
    description: string;
  }>;
}

export interface ReleaseGateStatus {
  release_id: string;
  overall_status: 'pass' | 'fail' | 'pending';
  checks: ReleaseCheck[];
}

export interface ReleaseCheck {
  name: string;
  status: 'pass' | 'fail' | 'pending';
  detail: string;
}

export interface BehaviorSpaceData {
  total_endpoints: number;
  covered_endpoints: number;
  auth_tested: number;
  data_validation_tested: number;
  business_logic_tested: number;
  routes: BehaviorRoute[];
}

export interface BehaviorRoute {
  method: string;
  path: string;
  tested: boolean;
  findings: number;
}

// Replay API 结果类型
export interface ReplayResult {
  ok: boolean;
  finding_id: string;
  request?: {
    method: string;
    url: string;
    headers: Record<string, string>;
    body?: string;
    timestamp: string;
  };
  response?: {
    status_code: number;
    headers: Record<string, string>;
    body: string;
    duration_ms: number;
  };
  success?: boolean;
  error?: string;
  original_evidence?: {
    status_code: number;
    response_body_excerpt: string;
    har_actor: string;
  };
  diff?: {
    status_match: boolean;
    body_match: boolean;
    key_differences: string[];
  };
}
