import { type ReactNode } from 'react';
import type { ConnectorRecord } from '../../api/client';
import type { SettingsTopologyViewModel } from '../../lib/settings-topology';

type SettingsTopologySectionProps = {
  project: string;
  topology: SettingsTopologyViewModel;
  onToggleSystem: (systemName: string) => void;
  onOpenCreateConnector: (systemName?: string) => void;
  onOpenEditConnector: (connector: ConnectorRecord) => void;
  onToggleConnectorStatus: (connector: ConnectorRecord, enabled: boolean) => void;
  children?: ReactNode;
};

export function SettingsTopologySection({
  project,
  topology,
  onToggleSystem,
  onOpenCreateConnector,
  onOpenEditConnector,
  onToggleConnectorStatus,
  children,
}: SettingsTopologySectionProps) {
  const {
    systemsCount,
    modulesCount,
    servicesCount,
    enabledConnectors,
    disabledConnectors,
    systems,
  } = topology;

  return (
    <div id="settings-system-access" className="section-card settings-span-2">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">必要步骤</span>
          <h2>接入被测系统</h2>
          <p className="settings-card-sub">
            只提供系统名称、测试环境地址和可用凭据；模块、服务结构、登录接口和数据关系由后台自动识别。
          </p>
        </div>
        {project && systemsCount > 0 && <button onClick={() => onOpenCreateConnector()} className="btn btn-primary settings-btn-compact">接入系统</button>}
      </div>

      <div className="settings-mini-stats">
        <div className="settings-mini-stat"><span>已接入系统</span><strong>{systemsCount}</strong></div>
        <div className="settings-mini-stat"><span>可用服务</span><strong>{enabledConnectors}</strong></div>
        <div className="settings-mini-stat"><span>后台识别模块</span><strong>{modulesCount}</strong></div>
      </div>

      {!project ? (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">等待客户</span>
          <h3>请先选择客户项目</h3>
          <p>选择客户后即可接入测试环境，其他结构信息由后台继续理解。</p>
        </section>
      ) : systemsCount === 0 ? (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">第一步</span>
          <h3>接入一个可验证的测试环境</h3>
          <p>不需要提前整理系统模块、接口分组或页面结构。</p>
          <button onClick={() => onOpenCreateConnector()} className="btn btn-primary">接入被测系统</button>
        </section>
      ) : (
        <details className="settings-auth-section">
          <summary>
            <strong>查看已接入结构</strong>
            <span className="muted">{systemsCount} 个系统 · {servicesCount} 个服务 · {disabledConnectors} 个停用</span>
          </summary>
          <div className="settings-card-note settings-mt-10">
            以下系统、模块和服务树是后台根据接入信息生成的管理视图，不要求用户持续维护。只有地址或凭据确实变化时才编辑。
          </div>
          <div className="settings-system-list">
            {systems.map((system) => (
              <div key={system.id} className="settings-system-card">
                <div className={`settings-system-head${system.expanded ? ' open' : ''}`}>
                  <button
                    type="button"
                    className="settings-system-toggle"
                    onClick={() => onToggleSystem(system.id)}
                    aria-expanded={system.expanded}
                    aria-controls={`settings-system-${system.id}`}
                  >
                    <div className="settings-system-head-main">
                      <span className="settings-system-caret">▸</span>
                      <div>
                        <strong>{system.label}</strong>
                        <span>{system.modulesCount} 模块 · {system.servicesCount} 服务</span>
                      </div>
                    </div>
                  </button>
                  <button onClick={() => onOpenCreateConnector(system.id)} className="btn btn-secondary settings-btn-mini">补充地址</button>
                </div>
                {system.expanded && (
                  <div id={`settings-system-${system.id}`} className="settings-system-body">
                    {system.modules.map((module) => (
                      <div key={module.id} className="settings-module-block">
                        <div className="settings-module-label">{module.label}</div>
                        {module.services.map((service) => (
                          <div
                            key={service.connector.connector_id}
                            id={`svc-${service.connector.connector_id}`}
                            className={`settings-service-row${service.highlight ? ' highlight' : ''}`}
                          >
                            <div className="settings-service-copy">
                              <div className="settings-service-name">
                                {service.serviceLabel}
                                <span className={`settings-service-badge ${service.connector.enabled ? 'enabled' : 'disabled'}`}>{service.connector.enabled ? '可用' : '停用'}</span>
                              </div>
                              <div className="settings-service-meta">
                                {service.endpointLabel}
                                {service.credentialLabel ? ` · 凭据: ${service.credentialLabel}` : ''}
                              </div>
                            </div>
                            <div className="settings-service-actions">
                              <button onClick={() => onOpenEditConnector(service.connector)} className="btn btn-secondary settings-btn-mini">地址或凭据变化</button>
                              {service.connector.enabled ? (
                                <button onClick={() => onToggleConnectorStatus(service.connector, false)} className="btn btn-secondary settings-btn-mini">停用</button>
                              ) : (
                                <button onClick={() => onToggleConnectorStatus(service.connector, true)} className="btn btn-secondary settings-btn-mini">启用</button>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </details>
      )}

      {children}
    </div>
  );
}
