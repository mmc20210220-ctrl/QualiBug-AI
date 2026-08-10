// Friendly labels only. This map is intentionally NOT an allowlist.
// Any unknown backend source_type must remain visible through the fallback.
const MATERIAL_TYPE_LABELS: Record<string, string> = {
  prd: 'PRD / 需求',
  requirement: '需求文档',
  requirements: '需求文档',
  openapi: 'API / 接口',
  api: 'API / 接口',
  database_schema: 'DB / 数据结构',
  db_design: 'DB / 数据设计',
  collaboration_document: '协作文档',
  historical_bug: '历史 Bug',
  ui_ux: 'UI / UX 设计',
  ui_design: 'UI 设计',
  ux_design: 'UX 设计',
  prototype: '原型 / 交互稿',
  design_spec: '设计规范',
  test_plan: '测试方案',
  test_case: '测试用例',
  architecture: '架构文档',
  architecture_design: '架构设计',
  permission: '权限说明',
  permission_design: '权限设计',
  business_process: '业务流程',
  data_dictionary: '数据字典',
  user_manual: '用户手册',
  deployment: '部署文档',
  release_note: '发布说明',
};

export function normalizeMaterialSourceType(value: unknown): string {
  return String(value || '').trim().toLowerCase() || 'unclassified';
}

export function materialSourceTypeLabel(type: string): string {
  if (type === 'unclassified') return '未分类资料';
  return MATERIAL_TYPE_LABELS[type] || type;
}
