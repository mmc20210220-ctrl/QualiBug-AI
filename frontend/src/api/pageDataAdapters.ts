import type {
  DashboardViewModel,
  EnvironmentViewModel,
  ExecutiveReportViewModel,
  LiveMapViewModel,
  RiskCardViewModel,
} from '../types/qualibug';

function asRecord(value: unknown): Record<string, any> {
  return value && typeof value === 'object' ? value as Record<string, any> : {};
}

function asArray(value: unknown): any[] {
  return Array.isArray(value) ? value : [];
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

export function toRiskCardViewModel(risk: unknown): RiskCardViewModel {
  const item = asRecord(risk);
  const flow = asRecord(item.affected_business_flow);
  return {
    riskId: asString(item.risk_id),
    title: asString(item.title, '未命名风险'),
    severity: asString(item.severity, 'unknown'),
    businessImpact: asString(item.business_impact, '暂无业务影响说明'),
    affectedFlowName: asString(flow.name, '未映射业务链路'),
    launchBlocking: item.launch_blocking === true,
    evidenceScore: asNumber(item.evidence_score),
    reproducibilityScore: asNumber(item.reproducibility_score),
    status: asString(item.status, 'detected'),
  };
}

export function toDashboardViewModel(data: unknown): DashboardViewModel {
  const snapshot = asRecord(data);
  const launch = asRecord(snapshot.launch_decision);
  const flowSummary = asRecord(snapshot.business_flow_summary);
  const riskSummary = asRecord(snapshot.risk_summary);
  const valueMetrics = asRecord(snapshot.value_metrics);
  const environment = asRecord(snapshot.environment_readiness);
  return {
    qualityHealthScore: asNumber(snapshot.quality_health_score),
    launchRecommendation: asString(launch.recommendation, 'UNKNOWN'),
    launchSummary: asString(launch.summary, ''),
    coreCoverageRate: asNumber(flowSummary.coverage_rate),
    launchBlockingRiskCount: Number(riskSummary.launch_blocking || 0),
    estimatedHoursSaved: asNumber(valueMetrics.estimated_hours_saved),
    environmentStatus: asString(environment.status, 'unknown'),
    topRisks: asArray(snapshot.top_risks).map(toRiskCardViewModel),
    executiveSummary: asString(snapshot.executive_summary, ''),
  };
}

export function toEnvironmentViewModel(data: unknown): EnvironmentViewModel {
  const env = asRecord(data);
  return {
    status: asString(env.status, 'unknown'),
    score: asNumber(env.score),
    allowFormalTest: env.allow_formal_test === true,
    safeExecutionMode: asString(env.safe_execution_mode, 'read_only'),
    blockers: asArray(env.current_blockers).map(String),
    suggestedActions: asArray(env.suggested_actions).map(String),
    requiredInputs: asArray(env.required_customer_inputs).map(asRecord),
  };
}

export function toLiveMapViewModel(data: unknown): LiveMapViewModel {
  const map = asRecord(data);
  const overlays = asArray(map.risk_overlays);
  const severities = overlays.map((overlay) => asString(asRecord(overlay).severity, 'unknown'));
  const highestRisk = severities.includes('critical') ? 'critical' : severities[0] || 'none';
  return {
    nodeCount: asArray(map.nodes).length,
    edgeCount: asArray(map.edges).length,
    riskOverlayCount: overlays.length,
    highestRisk,
    events: asArray(map.events).map(asRecord),
  };
}

export function toExecutiveReportViewModel(data: unknown): ExecutiveReportViewModel {
  const report = asRecord(data);
  return {
    title: asString(report.title, '质量风险评估报告'),
    launchRecommendation: asString(report.launch_recommendation, 'UNKNOWN'),
    executiveSummary: asString(report.executive_summary, ''),
    topRiskCount: asArray(report.top_risks).length,
    nextActionCount: asArray(report.next_actions).length,
  };
}
