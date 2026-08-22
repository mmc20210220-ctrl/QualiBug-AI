import { useCallback, useEffect, useState } from 'react';
import {
  getContinuousState,
  startContinuousScan,
  stopContinuousScan,
  type ContinuousState,
} from '../../api/continuous';
import { useToast } from '../useToast';

const INTERVAL_OPTIONS = [60, 120, 300];

type Props = { project: string };

/**
 * 持续守护控制面（P1-7）：把后端已有的 continuous start/stop/status 能力
 * 接入运行中心。状态与历史轮次全部来自后端真实回执，前端不推测进度。
 */
export function ContinuousGuardPanel({ project }: Props) {
  const toast = useToast();
  const [state, setState] = useState<ContinuousState | null>(null);
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [intervalSeconds, setIntervalSeconds] = useState(60);

  const refresh = useCallback(async () => {
    if (!project) return;
    try {
      const next = await getContinuousState(project);
      setState(next);
      setLoadError('');
    } catch (caught: unknown) {
      setLoadError(caught instanceof Error ? caught.message : '持续检测状态读取失败');
    }
  }, [project]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const handleStart = async () => {
    if (busy || !project) return;
    setBusy(true);
    try {
      const result = await startContinuousScan(project, intervalSeconds);
      toast.show(result.message, result.ok ? 'success' : 'danger');
    } catch (caught: unknown) {
      toast.show(caught instanceof Error ? caught.message : '持续检测启动失败', 'danger');
    } finally {
      setBusy(false);
      void refresh();
    }
  };

  const handleStop = async () => {
    if (busy || !project) return;
    setBusy(true);
    try {
      const result = await stopContinuousScan(project);
      toast.show(result.message, result.ok ? 'info' : 'danger');
    } catch (caught: unknown) {
      toast.show(caught instanceof Error ? caught.message : '持续检测停止失败', 'danger');
    } finally {
      setBusy(false);
      void refresh();
    }
  };

  if (!project) return null;

  const running = state?.status === 'scanning' || state?.status === 'waiting_for_project_scan';

  return (
    <details className="card mb-4" id="continuous-guard">
      <summary>
        <strong>持续守护</strong>
        <span className="muted">按固定间隔自动重复「运行前检查 → 真实检测」，直到覆盖收敛或手动停止</span>
      </summary>

      <div className="settings-grid mt-3">
        <div>
          <span className="muted">当前状态</span>
          <p>{state?.message || (loadError ? `读取失败：${loadError}` : '正在核对…')}</p>
        </div>
        <div>
          <span className="muted">累计轮次</span>
          <p>{state?.totalRuns ?? 0}</p>
        </div>
        <div>
          <span className="muted">最近一轮</span>
          <p>{state?.lastScan || '尚未运行'}</p>
        </div>
        <div>
          <span className="muted">最近结论</span>
          <p>{state && state.lastScan ? `发现 ${state.lastFindings} 条 · 覆盖 ${state.lastCoverage}%` : '—'}</p>
        </div>
      </div>

      <div className="settings-actions mt-3">
        {!running && (
          <>
            <label className="form-label" htmlFor="continuous-interval">间隔</label>
            <select
              id="continuous-interval"
              className="form-input settings-btn-mini"
              style={{ width: 'auto' }}
              value={intervalSeconds}
              onChange={(event) => setIntervalSeconds(Number(event.target.value) || 60)}
            >
              {INTERVAL_OPTIONS.map((seconds) => (
                <option key={seconds} value={seconds}>{seconds >= 60 ? `${seconds / 60} 分钟` : `${seconds} 秒`}</option>
              ))}
            </select>
            <button type="button" className="btn btn-primary settings-btn-mini" disabled={busy || !state} onClick={() => void handleStart()}>
              启动持续守护
            </button>
          </>
        )}
        {running && (
          <button type="button" className="btn btn-secondary settings-btn-mini" disabled={busy} onClick={() => void handleStop()}>
            停止持续守护
          </button>
        )}
        <button type="button" className="btn btn-secondary settings-btn-mini" onClick={() => void refresh()}>刷新状态</button>
      </div>

      <p className="settings-hint mt-3">
        持续守护与手动检测共用同一项目扫描租约：同一时刻只会执行一个检测任务，不会并发写入目标环境。每轮结果都会如实记录在下方历史中；空轮次不等于系统无问题。
      </p>

      {state && state.runs.length > 0 && (
        <details className="mt-3">
          <summary><strong>最近轮次历史</strong> <span className="muted">{state.runs.length}/{state.totalRuns} 轮</span></summary>
          <ul className="mt-3">
            {[...state.runs].reverse().map((run) => (
              <li key={`${run.timestamp}-${run.scan_id}`}>
                {run.timestamp} · 发现 {run.findings} 条 · 覆盖 {run.coverage}% · 评级 {run.grade || '未评级'}
              </li>
            ))}
          </ul>
        </details>
      )}
    </details>
  );
}
