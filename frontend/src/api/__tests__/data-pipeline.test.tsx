import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getFindingsMock = vi.fn();

vi.mock('../client', () => ({
  // data.ts 仅从 client 引入 getFindings / getKnowledgeAsset / getProjects；
  // 共享存储只消费 getFindings。
  getFindings: (...args: unknown[]) => getFindingsMock(...args),
  getKnowledgeAsset: vi.fn(),
  getProjects: vi.fn(),
}));

import {
  usePipelineData,
  usePipelineSnapshot,
  useProjectSummary,
  useScanCompletedRefresh,
} from '../data';

function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void } {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const OK_PAYLOAD = { resolvedProjectId: 'p1', projectId: 'p1', projectName: '客户一', status: 'idle', updatedAt: '', risks: [], scan_meta: {}, value_metrics: {}, executive_summary: {}, knowledge_summary: {}, campaign: {}, coverage_gaps: [] };

describe('共享 command-center 存储（usePipelineSnapshot 行为）', () => {
  beforeEach(() => {
    getFindingsMock.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('同项目多消费者：并发挂载合并为一次网络往返', async () => {
    getFindingsMock.mockReturnValue(new Promise(() => {}));
    const a = renderHook(() => usePipelineData('merge-p'));
    const b = renderHook(() => useProjectSummary('merge-p'));
    await waitFor(() => expect(getFindingsMock).toHaveBeenCalledTimes(1));
    a.unmount();
    b.unmount();
  });

  it('成功加载后所有订阅者拿到同一快照；失败时置空并显式暴露错误', async () => {
    const gate = deferred<unknown>();
    getFindingsMock.mockImplementation(() => gate.promise);

    // 每个用例使用独立 project id：模块级 store 跨用例持久，
    // 复用同一 id 会命中上一用例的在途请求守卫（fetching=true）。
    const summary = renderHook(() => useProjectSummary('load-p'));
    await waitFor(() => expect(summary.result.current.loading).toBe(true));

    gate.resolve(OK_PAYLOAD);
    await waitFor(() => {
      expect(summary.result.current.error).toBe('');
      expect(summary.result.current.projectName).toBe('客户一');
      expect(summary.result.current.hasResolvedProject).toBe(true);
    });
    summary.unmount();

    const fail = deferred<unknown>();
    getFindingsMock.mockImplementation(() => fail.promise);
    const second = renderHook(() => useProjectSummary('fail-p'));
    fail.reject(new Error('后端不可达'));
    await waitFor(() => {
      expect(second.result.current.error).toBe('后端不可达');
      // 故障不冒充结论：计数回零、项目名退化为占位而非陈旧数据
      expect(second.result.current.currentDefectCount).toBe(0);
    });
    second.unmount();
  });

  it('scan-completed 事件刷新既有订阅（无订阅时不产生孤儿请求）', async () => {
    getFindingsMock.mockResolvedValue(OK_PAYLOAD);
    // 现实形态：先有 store 订阅（初始加载），生命周期钩子再监听完成事件。
    const view = renderHook(() => {
      usePipelineSnapshot('event-p', 60_000);
      useScanCompletedRefresh('event-p');
    });
    await waitFor(() => expect(getFindingsMock).toHaveBeenCalledTimes(1));
    getFindingsMock.mockClear();

    act(() => {
      window.dispatchEvent(new CustomEvent('qualibug:scan-completed', { detail: { project: 'event-p' } }));
    });
    await waitFor(() => expect(getFindingsMock).toHaveBeenCalledTimes(1));

    // 无订阅的项目收到同一事件：不凭空创建请求。
    act(() => {
      window.dispatchEvent(new CustomEvent('qualibug:scan-completed', { detail: { project: 'no-subscriber-p' } }));
    });
    expect(getFindingsMock).toHaveBeenCalledTimes(1);
    view.unmount();
  });
});
