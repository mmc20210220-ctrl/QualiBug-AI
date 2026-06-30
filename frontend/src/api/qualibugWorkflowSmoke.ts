import { QualiBugCommandCenterClient } from './qualibugClient';
import { toDashboardViewModel, toEnvironmentViewModel, toLiveMapViewModel } from './pageDataAdapters';

// Phase104D framework-neutral frontend smoke workflow.
// Start backend first:
// python -m ai_test_asset_center.phase104_command_center_http_api --seed-scenario manufacturing --port 8790

const client = new QualiBugCommandCenterClient('http://127.0.0.1:8790');

function unwrap<T>(envelope: { success: boolean; data: T | null; error: any }): T {
  if (!envelope.success || envelope.data == null) {
    throw new Error(envelope.error?.message || 'QualiBug API request failed');
  }
  return envelope.data;
}

export async function runQualiBugFrontendWorkflowSmoke() {
  const created = unwrap(await client.createProject({
    customer_name: 'Frontend Demo Customer',
    project_name: 'Frontend Integration Smoke',
    system_name: 'ERP Demo System',
    industry: 'manufacturing',
    system_type: 'ERP',
    test_goal: '前端联调冒烟验证',
  }));
  const projectId = (created as any).project_id;

  await client.applyBusinessTemplate(projectId, { template_id: 'industry_manufacturing' });
  await client.patchEnvironmentConfig(projectId, {
    base_url: 'https://demo.example.local',
    auth_type: 'username_password_csrf',
    session_health_path: '/api/me',
    api_smoke_paths: ['/api/orders', '/api/reports/summary'],
  });
  const environment = toEnvironmentViewModel(unwrap(await client.runEnvironmentPreflight(projectId, {})));
  await client.generateTestPlan(projectId, {});
  await client.startTestRun(projectId, {});
  await client.generateExecutiveReport(projectId, {});

  const dashboard = toDashboardViewModel(unwrap(await client.getCommandCenter(projectId)));
  const liveMap = toLiveMapViewModel(unwrap(await client.getLiveMap(projectId)));

  return { projectId, environment, dashboard, liveMap };
}
