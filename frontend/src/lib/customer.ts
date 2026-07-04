import type { CustomerWorkspace } from '../api/client';

export type WorkspaceOption = {
  id: string;
  label: string;
};

export function formatCustomerName(value: string) {
  const normalized = String(value || '').trim();
  if (!normalized || normalized === 'default' || /^default tenant$/i.test(normalized)) {
    return '默认客户';
  }
  return normalized;
}

export function toWorkspaceOption(workspace: CustomerWorkspace): WorkspaceOption | null {
  const id = String(workspace.project_id || '').trim();
  if (!id) return null;
  return {
    id,
    label: formatCustomerName(String(workspace.customer_name || workspace.project_name || workspace.project_id || '').trim() || '未命名客户'),
  };
}

export function toWorkspaceOptions(workspaces: CustomerWorkspace[]) {
  return workspaces
    .map((workspace) => toWorkspaceOption(workspace))
    .filter((workspace): workspace is WorkspaceOption => Boolean(workspace));
}
