import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./session', () => {
  class ApiError extends Error {
    status: number;

    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }
  return {
    API_V1_BASE: '/api/v1',
    ApiError,
    fetchJSON: vi.fn(),
  };
});

import { fetchJSON } from './session';
import { getFinding } from './finding-detail';

describe('getFinding', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('reads only the requested finding endpoint and preserves evidence fields', async () => {
    vi.mocked(fetchJSON).mockResolvedValue({
      ok: true,
      data: {
        id: 'FIND-123',
        title: '订单状态异常',
        recommended_fix: 'should never reach customer UI',
        technical_details: {
          possible_root_cause: 'internal guess',
          trace_id: 'trace-1',
        },
        evidence_chain: [{ kind: 'http', summary: 'POST /orders' }],
        raw_evidence: {
          request_raw: { method: 'POST', path: '/orders' },
          response_raw: { status_code: 500 },
        },
        reproduction: { method: 'POST', path: '/orders', steps: ['创建订单'] },
        expected_actual_comparison: {
          expected: 'paid',
          actual: 'pending',
          difference: 'state mismatch',
        },
      },
    });

    const finding = await getFinding('acme', 'FIND-123');

    expect(fetchJSON).toHaveBeenCalledTimes(1);
    expect(fetchJSON).toHaveBeenCalledWith('/api/v1/projects/acme/findings/FIND-123');
    expect(finding?.evidence_chain).toEqual([{ kind: 'http', summary: 'POST /orders' }]);
    expect(finding?.raw_evidence?.request_raw?.path).toBe('/orders');
    expect(finding?.reproduction?.steps).toEqual(['创建订单']);
    expect(finding?.expected_actual_comparison?.difference).toBe('state mismatch');
    expect((finding as unknown as Record<string, unknown>)?.recommended_fix).toBeUndefined();
    expect((finding?.technical_details as Record<string, unknown>)?.possible_root_cause).toBeUndefined();
    expect(finding?.product_responsibility_boundary?.no_fix_advice).toBe(true);
  });
});
