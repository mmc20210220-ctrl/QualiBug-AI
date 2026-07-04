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

export interface Finding {
  id: string;
  title: string;
  severity: 'P0' | 'P1' | 'P2';
  defect_family: string;
  defect_family_label: string;
  risk_type: string;
  reporting_bucket: string;
  reporting_bucket_label: string;
  quality_assurance_gap: boolean;
  verdict: string;
  reproducibility_count: number;
  timestamp: string;
  evidence_chain: EvidenceStep[];
  proof: {
    hash: string;
    script_path: string;
    repro_rate: number;
  };
  expected: string;
  actual: string;
  repro_steps: string[];
  repro_method: string;
  repro_path: string;
  source_entity: string;
  source_value: string;
  docRefs?: Array<{ source_id?: string; display_name?: string; excerpt?: string; type?: string }>;
  evidence_hint: string;
  business_impact: { summary: string; urgency: string; module: string };
  investigation_guidance: {
    primary_area: string;
    relevant_apis: string[];
    relevant_tables: string[];
    log_search: string;
    sql_verify: string;
    trace_id: string;
  };
  reproduce_steps_business: string[];
}

export interface EvidenceStep {
  tag: 'rule' | 'api' | 'fact';
  label: string;
  content: string;
  detail: string;
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
