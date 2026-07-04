export type DefectFamilyId =
  | 'scenario_flow'
  | 'api_contract'
  | 'security_boundary'
  | 'privacy_compliance'
  | 'observability'
  | 'configuration_drift'
  | 'data_integrity'
  | 'performance'
  | 'stability'
  | 'compatibility'
  | 'ui'
  | 'uiux'
  | 'accessibility_i18n';

type DefectFamilyMeta = {
  label: string;
  reporting_bucket: string;
  bucket_label: string;
};

export type FindingTaxonomy = {
  defect_family: DefectFamilyId;
  defect_family_label: string;
  reporting_bucket: string;
  reporting_bucket_label: string;
  quality_assurance_gap: boolean;
};

type TaxonomyInput = {
  title?: string;
  risk_type?: string;
  defect_family?: string;
  category?: string;
  reporting_bucket?: string;
  repro_path?: string;
  quality_assurance_gap?: boolean;
};

export const DEFECT_FAMILY_ORDER: DefectFamilyId[] = [
  'scenario_flow',
  'api_contract',
  'security_boundary',
  'privacy_compliance',
  'data_integrity',
  'performance',
  'stability',
  'compatibility',
  'ui',
  'uiux',
  'accessibility_i18n',
  'observability',
  'configuration_drift',
];

export const DEFECT_FAMILY_META: Record<DefectFamilyId, DefectFamilyMeta> = {
  scenario_flow: { label: '场景流转', reporting_bucket: 'functional', bucket_label: '功能' },
  api_contract: { label: '接口契约', reporting_bucket: 'api', bucket_label: '接口' },
  security_boundary: { label: '安全边界', reporting_bucket: 'security', bucket_label: '安全' },
  privacy_compliance: { label: '隐私合规', reporting_bucket: 'security', bucket_label: '安全' },
  observability: { label: '可观测性', reporting_bucket: 'reliability', bucket_label: '可靠性' },
  configuration_drift: { label: '配置漂移', reporting_bucket: 'reliability', bucket_label: '可靠性' },
  data_integrity: { label: '数据一致性', reporting_bucket: 'data', bucket_label: '数据' },
  performance: { label: '性能', reporting_bucket: 'performance', bucket_label: '性能' },
  stability: { label: '稳定性', reporting_bucket: 'stability', bucket_label: '稳定性' },
  compatibility: { label: '兼容性', reporting_bucket: 'compatibility', bucket_label: '兼容性' },
  ui: { label: '界面呈现', reporting_bucket: 'frontend', bucket_label: '前端' },
  uiux: { label: '交互体验', reporting_bucket: 'ux', bucket_label: '体验' },
  accessibility_i18n: { label: '可访问性/本地化', reporting_bucket: 'ux', bucket_label: '体验' },
};

const RISK_TYPE_TO_FAMILY: Record<string, DefectFamilyId> = {
  permission_bypass: 'security_boundary',
  idor: 'security_boundary',
  tenant_isolation: 'security_boundary',
  openapi_security_static_scan: 'security_boundary',
  audit_privacy_probe: 'privacy_compliance',
  privacy_compliance: 'privacy_compliance',
  sensitive_field_leak: 'privacy_compliance',
  audit_log_missing: 'privacy_compliance',
  desensitization_failure: 'privacy_compliance',
  business_invariant: 'data_integrity',
  business_reconciliation: 'data_integrity',
  business_causality: 'data_integrity',
  consistency_integrity: 'data_integrity',
  unique_constraint: 'data_integrity',
  date_order: 'data_integrity',
  idempotency: 'data_integrity',
  stock_consistency: 'data_integrity',
  metamorphic_relation: 'data_integrity',
  temporal_data_regression: 'data_integrity',
  business_population_constraint: 'data_integrity',
  payment: 'data_integrity',
  refund: 'data_integrity',
  lifecycle_integrity: 'scenario_flow',
  business_reasoning: 'scenario_flow',
  event_chain_integrity: 'scenario_flow',
  saga_compensation: 'scenario_flow',
  coupon_abuse: 'scenario_flow',
  api_contract: 'api_contract',
  positive_numeric: 'api_contract',
  nonnegative_numeric: 'api_contract',
  enum_closed_set: 'api_contract',
  api_backward_compatibility: 'compatibility',
  compatibility: 'compatibility',
  performance_regression: 'performance',
  stability_timeout: 'stability',
  frontend_execution_runtime: 'ui',
  frontend_runtime: 'ui',
  frontend_ui: 'ui',
  browser_ui_replay: 'ui',
  frontend_ux: 'uiux',
  assurance_coverage_gap: 'observability',
  quality_assurance_gap: 'observability',
  deployment_config_drift: 'configuration_drift',
};

function isKnownFamily(value: string): value is DefectFamilyId {
  return value in DEFECT_FAMILY_META;
}

function inferFromTitle(title: string, reproPath: string): DefectFamilyId {
  const text = title.toLowerCase();
  if (
    text.includes('401') || text.includes('403') || text.includes('权限') || text.includes('越权')
    || text.includes('tenant') || text.includes('idor') || text.includes('鉴权')
  ) return 'security_boundary';
  if (
    text.includes('隐私') || text.includes('合规') || text.includes('脱敏') || text.includes('敏感')
    || text.includes('audit') || text.includes('审计') || text.includes('泄露')
  ) return 'privacy_compliance';
  if (
    text.includes('trace') || text.includes('日志') || text.includes('观测') || text.includes('告警')
    || text.includes('error code') || text.includes('operationid')
  ) return 'observability';
  if (
    text.includes('配置') || text.includes('环境') || text.includes('开关') || text.includes('部署')
    || text.includes('config') || text.includes('env')
  ) return 'configuration_drift';
  if (
    text.includes('db verified') || text.includes('数据') || text.includes('一致性') || text.includes('库存')
    || text.includes('bom') || text.includes('流水') || text.includes('对账') || text.includes('金额')
    || text.includes('支付') || text.includes('退款') || text.includes('幂等') || text.includes('idempot')
  ) return 'data_integrity';
  if (
    text.includes('性能') || text.includes('latency') || text.includes('slow') || text.includes('吞吐')
    || text.includes('内存') || text.includes('fanout')
  ) return 'performance';
  if (
    text.includes('timeout') || text.includes('超时') || text.includes('重试') || text.includes('抖动')
    || text.includes('间歇') || text.includes('storm')
  ) return 'stability';
  if (
    text.includes('兼容') || text.includes('compat') || text.includes('版本')
  ) return 'compatibility';
  if (
    text.includes('本地化') || text.includes('i18n') || text.includes('locale')
    || text.includes('timezone') || text.includes('时区') || text.includes('无障碍')
  ) return 'accessibility_i18n';
  if (
    text.includes('ui') || text.includes('页面') || text.includes('渲染') || text.includes('route')
    || text.includes('导航') || text.includes('空白')
  ) return 'ui';
  if (
    text.includes('ux') || text.includes('体验') || text.includes('反馈') || text.includes('cta')
    || text.includes('交互') || text.includes('可用性')
  ) return 'uiux';
  if (
    reproPath || text.includes('openapi') || text.includes('schema') || text.includes('contract')
    || text.includes('spec')
  ) return 'api_contract';
  return 'scenario_flow';
}

export function resolveFindingTaxonomy(input: TaxonomyInput): FindingTaxonomy {
  const explicitFamily = String(input.defect_family || '').trim();
  const riskType = String(input.risk_type || input.category || '').trim();
  const title = String(input.title || '');
  const reproPath = String(input.repro_path || '');
  const rawBucket = String(input.reporting_bucket || '').trim();
  const qualityGap = Boolean(input.quality_assurance_gap);

  const family = isKnownFamily(explicitFamily)
    ? explicitFamily
    : (RISK_TYPE_TO_FAMILY[riskType] || inferFromTitle(title, reproPath));
  const meta = DEFECT_FAMILY_META[family];

  return {
    defect_family: family,
    defect_family_label: meta.label,
    reporting_bucket: rawBucket || meta.reporting_bucket,
    reporting_bucket_label: meta.bucket_label,
    quality_assurance_gap: qualityGap,
  };
}

export function getDefectFamilyLabel(family: string): string {
  return isKnownFamily(family) ? DEFECT_FAMILY_META[family].label : '其他类型';
}
