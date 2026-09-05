import { describe, expect, it } from 'vitest';
import {
  buildSettingsTopologyConnectors,
  serviceConfigUpdatePayload,
} from '../settings-utils';

describe('settings service topology', () => {
  it('uses service credentials as the canonical topology when the registry is empty', () => {
    const connectors = buildSettingsTopologyConnectors([
      { name: 'gateway', base_url: 'http://localhost:8080', enabled: true },
    ], []);

    expect(connectors).toHaveLength(1);
    expect(connectors[0]).toMatchObject({
      connector_id: 'service_config:gateway',
      display_name: 'gateway',
      system_name: 'gateway',
      endpoint_ref: 'http://localhost:8080',
      enabled: true,
    });
  });

  it('merges legacy metadata without duplicating a canonical service', () => {
    const connectors = buildSettingsTopologyConnectors(
      [{ name: 'gateway', base_url: 'http://localhost:8080', enabled: false }],
      [{
        connector_id: 'legacy-gateway',
        kind: 'http_api',
        display_name: 'gateway',
        enabled: true,
        module_name: 'public',
      }],
    );

    expect(connectors).toHaveLength(1);
    expect(connectors[0]).toMatchObject({
      connector_id: 'legacy-gateway',
      module_name: 'public',
      enabled: false,
    });
  });

  it('keeps masked credentials in an enabled-state update payload', () => {
    const payload = serviceConfigUpdatePayload({
      name: 'gateway',
      base_url: 'http://localhost:8080',
      enabled: true,
      auth: {
        type: 'password_login',
        login_api: '/auth/login',
        admin: { username: 'qa@example.test', password: '********' },
      },
    }, { enabled: false });

    expect(payload).toMatchObject({
      name: 'gateway',
      base_url: 'http://localhost:8080',
      enabled: false,
      role_accounts: [{ role: 'admin', username: 'qa@example.test', password: '********' }],
    });
  });
});
