import { useEffect, useState } from 'react';
import { getHealth } from '../../api/client';

type HealthState = 'checking' | 'available' | 'unavailable';

type RecordValue = Record<string, unknown>;

function asRecord(value: unknown): RecordValue {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {};
}

function isVerifiedApiHealth(payload: unknown): boolean {
  const root = asRecord(payload);
  const components = asRecord(root.components);
  const api = asRecord(components.api);
  return root.status === 'healthy' && api.status === 'healthy';
}

const healthLabels: Record<HealthState, string> = {
  checking: '正在检查登录服务',
  available: '登录服务可用',
  unavailable: '登录服务不可用',
};

export function ServiceHealthBadge() {
  const [state, setState] = useState<HealthState>('checking');

  useEffect(() => {
    let active = true;

    const checkHealth = async () => {
      try {
        const payload = await getHealth();
        if (!isVerifiedApiHealth(payload)) {
          throw new Error('Health payload did not verify API availability');
        }
        if (active) setState('available');
      } catch (error: unknown) {
        console.error('[login.health] Login service health check failed', { error });
        if (active) setState('unavailable');
      }
    };

    void checkHealth();
    return () => { active = false; };
  }, []);

  const label = healthLabels[state];
  return (
    <div
      className={`login-health-badge is-${state}`}
      role="status"
      aria-live="polite"
      aria-label={`登录服务状态：${label}`}
    >
      <span className="login-health-dot" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
