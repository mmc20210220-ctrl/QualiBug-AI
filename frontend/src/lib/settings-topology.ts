import type { ConnectorRecord } from '../api/client';

export type SettingsTopologyServiceItem = {
  connector: ConnectorRecord;
  highlight: boolean;
  serviceLabel: string;
  endpointLabel: string;
  credentialLabel: string;
};

export type SettingsTopologyModuleItem = {
  id: string;
  label: string;
  services: SettingsTopologyServiceItem[];
};

export type SettingsTopologySystemItem = {
  id: string;
  label: string;
  expanded: boolean;
  modulesCount: number;
  servicesCount: number;
  modules: SettingsTopologyModuleItem[];
};

export type SettingsTopologyViewModel = {
  systemsCount: number;
  modulesCount: number;
  servicesCount: number;
  enabledConnectors: number;
  disabledConnectors: number;
  systems: SettingsTopologySystemItem[];
};

export function buildSettingsTopologyViewModel(
  connectors: ConnectorRecord[],
  expandedSystems: Set<string>,
  highlightId: string,
  credentialLabel: (value?: string) => string,
): SettingsTopologyViewModel {
  const systemMap = new Map<string, Map<string, ConnectorRecord[]>>();

  connectors.forEach((connector) => {
    const systemName = String(connector.system_name || '未分类系统').trim();
    const moduleName = String(connector.module_name || 'default').trim();
    if (!systemMap.has(systemName)) systemMap.set(systemName, new Map());

    const moduleMap = systemMap.get(systemName)!;
    if (!moduleMap.has(moduleName)) moduleMap.set(moduleName, []);
    moduleMap.get(moduleName)!.push(connector);
  });

  const systems = Array.from(systemMap.entries()).map(([systemName, moduleMap]) => {
    const modules = Array.from(moduleMap.entries()).map(([moduleName, items]) => ({
      id: `${systemName}::${moduleName}`,
      label: moduleName !== 'default' ? moduleName : '默认模块',
      services: items.map((connector) => ({
        connector,
        highlight: highlightId === connector.connector_id,
        serviceLabel: connector.module_name || connector.display_name || '未命名服务',
        endpointLabel: connector.endpoint_ref || '未配置地址',
        credentialLabel: connector.credential_ref ? credentialLabel(connector.credential_ref) : '',
      })),
    }));

    return {
      id: systemName,
      label: systemName,
      expanded: expandedSystems.has(systemName),
      modulesCount: modules.length,
      servicesCount: modules.reduce((sum, module) => sum + module.services.length, 0),
      modules,
    };
  });

  const enabledConnectors = connectors.filter((item) => item.enabled).length;
  return {
    systemsCount: systems.length,
    modulesCount: systems.reduce((sum, system) => sum + system.modulesCount, 0),
    servicesCount: connectors.length,
    enabledConnectors,
    disabledConnectors: Math.max(0, connectors.length - enabledConnectors),
    systems,
  };
}

