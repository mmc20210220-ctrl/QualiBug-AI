import { useSearchParams, useLocation } from 'react-router-dom';
import { useState, useCallback } from 'react';
import { runScan } from '../api/client';
import { useLiveStatus } from '../api/data';
import { useToast } from './Toast';

const pageLabels: Record<string, string> = {
  '/dashboard': '风险总览',
  '/findings': '行为裂隙',
  '/evidence': '证据链',
  '/behavior-space': '行为空间',
  '/materials': '企业资料',
  '/release': '发布门禁',
  '/settings': '设置',
};

export function Topbar() {
  const [params] = useSearchParams();
  const location = useLocation();
  const project = params.get('project') || 'real_project_demo';
  const [scanning, setScanning] = useState(false);
  const { lastScanMinutes, scanActive } = useLiveStatus(project, 15000);
  const toast = useToast();

  const currentPage = pageLabels[location.pathname] || '风险总览';

  const handleScan = useCallback(async () => {
    setScanning(true);
    toast.show('扫描已启动，正在分析系统行为...', 'info');
    try {
      await runScan(project);
      toast.show('扫描完成！请刷新页面查看最新结果', 'success');
    } catch (e: any) {
      toast.show(`扫描失败: ${e.message}`, 'danger');
    }
    setTimeout(() => setScanning(false), 3000);
  }, [project, toast]);

  const minutesDisplay = lastScanMinutes !== null
    ? (lastScanMinutes < 1 ? '刚刚' : `${lastScanMinutes} 分钟前`)
    : '--';

  return (
    <header className="topbar">
      <span className="breadcrumb">QualiBug <b>/ {currentPage}</b></span>
      <div className="topbar-right">
        <span className={`system-status ${scanActive ? '' : 'online'}`}
          style={scanActive ? { background: 'var(--warning-muted)', color: 'var(--warning)' } : {}}>
          <span className="pulse-dot" style={{
            width: 6, height: 6, borderRadius: '50%',
            background: scanActive ? 'var(--warning)' : 'var(--success)',
            display: 'inline-block',
          }} />
          {scanActive ? '扫描运行中...' : `行为空间监控中 · 最近扫描 ${minutesDisplay}`}
        </span>
        <button className="btn btn-primary" onClick={handleScan} disabled={true} title="已加载完整数据，无需重扫">
          ▶ 数据已就绪
        </button>
        <div className="avatar" title={project}>QB</div>
      </div>
    </header>
  );
}
