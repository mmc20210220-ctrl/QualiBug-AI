import { describe, expect, it, vi } from 'vitest';

const fetchWithAuthMock = vi.fn();

vi.mock('../client', () => ({
  fetchWithAuth: (...args: unknown[]) => fetchWithAuthMock(...args),
}));

import { cancelActiveScan } from '../run-center';

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('cancelActiveScan（协作式取消前端 API）', () => {
  it('空项目：不发起请求，诚实返回未选择', async () => {
    const result = await cancelActiveScan('   ');
    expect(result.requested).toBe(false);
    expect(result.reason_code).toBe('NO_PROJECT');
    expect(fetchWithAuthMock).not.toHaveBeenCalled();
  });

  it('200 已登记：requested=true 且透出服务端 message', async () => {
    fetchWithAuthMock.mockResolvedValue(jsonResponse(200, {
      ok: true,
      requested: true,
      reason_code: 'SCAN_CANCEL_REQUESTED',
      message: '取消请求已登记，将在当前实验边界安全停止。',
    }));
    const result = await cancelActiveScan('p1');
    expect(fetchWithAuthMock).toHaveBeenCalledWith('/api/v1/scan/cancel', expect.objectContaining({ method: 'POST' }));
    expect(result.requested).toBe(true);
    expect(result.message).toContain('实验边界');
  });

  it('409 无运行中任务：requested=false 但不作为传输错误抛出', async () => {
    fetchWithAuthMock.mockResolvedValue(jsonResponse(409, {
      ok: false,
      requested: false,
      reason_code: 'NO_ACTIVE_SCAN',
      message: '当前没有正在运行的检测任务。',
    }));
    const result = await cancelActiveScan('p1');
    expect(result.requested).toBe(false);
    expect(result.reason_code).toBe('NO_ACTIVE_SCAN');
  });

  it('非 JSON 错误体且非 2xx：抛出携带状态码的传输错误', async () => {
    fetchWithAuthMock.mockResolvedValue(new Response('gateway timeout', { status: 504 }));
    await expect(cancelActiveScan('p1')).rejects.toThrow(/HTTP 504/);
  });
});
