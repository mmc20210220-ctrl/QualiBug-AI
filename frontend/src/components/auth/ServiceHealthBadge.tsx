import { useEffect, useState } from 'react';
import { getHealth } from '../../api/client';
import { asRecord } from '../../lib/value-guards';

type HealthState = 'checking' | 'available' | 'unavailable';

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

let healthCheck: Promise<HealthState> | null = null;

function getVerifiedHealthState(): Promise<HealthState> {
  if (healthCheck) return healthCheck;
  const request = getHealth()
    .then<HealthState>((payload) => {
      if (!isVerifiedApiHealth(payload)) {
        throw new Error('Health payload did not verify API availability');
      }
      return 'available';
    })
    .catch<HealthState>((error: unknown) => {
      console.error('[login.health] Login service health check failed', { error });
      return 'unavailable';
    });
  healthCheck = request;
  return request;
}

export function ServiceHealthBadge() {
  const [state, setState] = useState<HealthState>('checking');

  useEffect(() => {
    let active = true;

    void getVerifiedHealthState().then((nextState) => {
      if (active) setState(nextState);
    });
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
