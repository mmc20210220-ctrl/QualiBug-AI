// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SettingsTopologySection } from '../SettingsTopologySection';
import type { SettingsTopologyViewModel } from '../../../lib/settings-topology';

const EMPTY_TOPOLOGY: SettingsTopologyViewModel = {
  systemsCount: 0,
  modulesCount: 0,
  servicesCount: 0,
  enabledConnectors: 0,
  disabledConnectors: 0,
  systems: [],
};

describe('SettingsTopologySection', () => {
  it('does not present a failed connector read as an empty setup', () => {
    const onRetry = vi.fn();

    render(
      <SettingsTopologySection
        project="project-a"
        topology={EMPTY_TOPOLOGY}
        loadError="连接器服务返回 503"
        onRetry={onRetry}
        onToggleSystem={vi.fn()}
        onOpenCreateConnector={vi.fn()}
        onOpenEditConnector={vi.fn()}
        onToggleConnectorStatus={vi.fn()}
      />,
    );

    expect(screen.getByRole('alert').textContent).toContain('系统接入状态暂不可用');
    expect(screen.getByText('连接器服务返回 503')).toBeTruthy();
    expect(screen.queryByText('接入一个可验证的测试环境')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '重新读取' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
