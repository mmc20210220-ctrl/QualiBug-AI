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
    <div className="section-card settings-span-2">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">接入治理</span>
          <h2>系统接入</h2>
          <p className="settings-card-sub">{systemsCount} 个系统 · {modulesCount} 个模块 · {servicesCount} 个服务</p>
        </div>
        {project && <button onClick={() => onOpenCreateConnector()} className="btn btn-primary settings-btn-compact">新增服务</button>}
      </div>
      <div className="settings-mini-stats">
        <div className="settings-mini-stat"><span>已启用</span><strong>{enabledConnectors}</strong></div>
        <div className="settings-mini-stat"><span>已停用</span><strong>{disabledConnectors}</strong></div>
        <div className="settings-mini-stat"><span>覆盖模块</span><strong>{modulesCount}</strong></div>
      </div>

      {!project ? (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">等待选择</span>
          <h3>请先选择客户</h3>
          <p>选择客户后，才能继续管理服务接入、数据库凭证引用与模块结构。</p>
        </section>
      ) : systemsCount === 0 ? (
        <section className="findings-empty-state compact">
          <span className="findings-empty-kicker">当前空态</span>
          <h3>尚未接入任何系统</h3>
          <p>从这里开始纳管第一个业务服务，后续系统、模块和服务会自动形成结构树。</p>
          <button onClick={() => onOpenCreateConnector()} className="btn btn-primary">接入第一个服务</button>
        </section>
      ) : (
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
                <button onClick={() => onOpenCreateConnector(system.id)} className="btn btn-secondary settings-btn-mini">新增同系统服务</button>
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
                              <span className={`settings-service-badge ${service.connector.enabled ? 'enabled' : 'disabled'}`}>{service.connector.enabled ? '启用' : '停用'}</span>
                            </div>
                            <div className="settings-service-meta">
                              {service.endpointLabel}
                              {service.credentialLabel ? ` · 数据源: ${service.credentialLabel}` : ''}
                            </div>
                          </div>
                          <div className="settings-service-actions">
                            <button onClick={() => onOpenEditConnector(service.connector)} className="btn btn-secondary settings-btn-mini">编辑</button>
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
      )}

      {children}
    </div>
  );
}
