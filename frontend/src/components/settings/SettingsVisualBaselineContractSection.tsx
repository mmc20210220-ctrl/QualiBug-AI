import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  listVisualBaselines,
  VISUAL_BASELINES_CHANGED_EVENT,
  type VisualBaselineRecord,
} from '../../api/visual-baselines';
import '../../styles/visual-baseline-contract.css';

type SettingsVisualBaselineContractSectionProps = {
  project: string;
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || '加载失败');
}

function parseMaskSelectors(value: string): string[] {
  return value
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 64);
}

function validStartUrl(value: string): boolean {
  if (value.startsWith('/') && !value.startsWith('//')) return true;
  return /^https?:\/\/[^\s]+$/i.test(value);
}

export function SettingsVisualBaselineContractSection({ project }: SettingsVisualBaselineContractSectionProps) {
  const outputRef = useRef<HTMLTextAreaElement | null>(null);
  const [records, setRecords] = useState<VisualBaselineRecord[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [startUrl, setStartUrl] = useState('');
  const [changedRatio, setChangedRatio] = useState('0.001');
  const [channelTolerance, setChannelTolerance] = useState('2');
  const [maskSelectors, setMaskSelectors] = useState('');
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');

  const loadActiveBaselines = useCallback(async (showLoading = true) => {
    if (!project) {
      setRecords([]);
      setSelectedId('');
      setStatus('');
      return;
    }
    if (showLoading) setLoading(true);
    try {
      const inventory = await listVisualBaselines(project);
      const active = inventory.baselines
        .filter((record) => record.status === 'active')
        .sort((left, right) => {
          const approved = Number(right.authority === 'approved_copy')
            - Number(left.authority === 'approved_copy');
          if (approved !== 0) return approved;
          return String(right.created_at_utc || '').localeCompare(String(left.created_at_utc || ''));
        });
      setRecords(active);
      setSelectedId((current) => (
        active.some((record) => record.baseline_id === current)
          ? current
          : active[0]?.baseline_id || ''
      ));
    } catch (error: unknown) {
      setRecords([]);
      setSelectedId('');
      setStatus(`加载失败：${errorMessage(error)}`);
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [project]);

  useEffect(() => {
    let cancelled = false;
    setStartUrl('');
    setMaskSelectors('');
    if (!project) {
      setRecords([]);
      setSelectedId('');
      setStatus('');
      return () => { cancelled = true; };
    }
    setLoading(true);
    setStatus('');
    listVisualBaselines(project)
      .then((inventory) => {
        if (cancelled) return;
        const active = inventory.baselines
          .filter((record) => record.status === 'active')
          .sort((left, right) => {
            const approved = Number(right.authority === 'approved_copy')
              - Number(left.authority === 'approved_copy');
            if (approved !== 0) return approved;
            return String(right.created_at_utc || '').localeCompare(String(left.created_at_utc || ''));
          });
        setRecords(active);
        setSelectedId((current) => (
          active.some((record) => record.baseline_id === current)
            ? current
            : active[0]?.baseline_id || ''
        ));
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setRecords([]);
          setSelectedId('');
          setStatus(`加载失败：${errorMessage(error)}`);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [project]);

  useEffect(() => {
    const handleRegistryChange = (event: Event) => {
      const detail = event instanceof CustomEvent
        ? event.detail as { project?: unknown }
        : {};
      if (typeof detail.project === 'string' && detail.project !== project) return;
      void loadActiveBaselines(false);
    };
    window.addEventListener(VISUAL_BASELINES_CHANGED_EVENT, handleRegistryChange);
    return () => {
      window.removeEventListener(VISUAL_BASELINES_CHANGED_EVENT, handleRegistryChange);
    };
  }, [loadActiveBaselines, project]);

  const selected = useMemo(
    () => records.find((record) => record.baseline_id === selectedId) || null,
    [records, selectedId],
  );

  const validation = useMemo(() => {
    const target = startUrl.trim();
    const ratio = Number(changedRatio);
    const tolerance = Number(channelTolerance);
    if (!selected) return { ok: false, message: '请选择活动视觉基线。' };
    if (!target) return { ok: false, message: '请填写被测页面路径或 URL。' };
    if (target.length > 2000 || !validStartUrl(target)) {
      return {
        ok: false,
        message: '页面地址必须是单斜杠开头的相对路径，或 http/https 绝对 URL。',
      };
    }
    if (!Number.isFinite(ratio) || ratio < 0 || ratio > 1) {
      return { ok: false, message: '允许变化比例必须是 0–1 之间的数字。' };
    }
    if (!Number.isInteger(tolerance) || tolerance < 0 || tolerance > 32) {
      return { ok: false, message: '通道容差必须是 0–32 之间的整数。' };
    }
    return { ok: true, message: '' };
  }, [changedRatio, channelTolerance, selected, startUrl]);

  const contractFragment = useMemo(() => {
    if (!selected || !validation.ok) return '';
    const target = startUrl.trim();
    return JSON.stringify({
      request_id: `visual_${selected.baseline_id}`,
      title: `Visual baseline ${selected.ref}`,
      provider: 'playwright_browser_plan',
      start_url: target,
      execution_mode: 'safe_read_only',
      browser_plan: {
        execution_mode: 'safe_read_only',
        steps: [
          {
            action: 'set_viewport',
            width: selected.viewport_width,
            height: selected.viewport_height,
          },
          {
            action: 'goto',
            url: target,
          },
          {
            action: 'expect_visual_baseline',
            baseline_ref: selected.ref,
            baseline_sha256: selected.sha256,
            max_changed_pixel_ratio: Number(changedRatio),
            channel_tolerance: Number(channelTolerance),
            full_page: selected.full_page,
            animations_disabled: true,
            renderer_profile: selected.renderer_profile,
            scroll_origin: selected.scroll_origin,
            font_readiness: selected.font_readiness,
            viewport_width: selected.viewport_width,
            viewport_height: selected.viewport_height,
            mask_selectors: parseMaskSelectors(maskSelectors),
            mask_locator_intents: [],
            mask_regions: [],
          },
        ],
      },
    }, null, 2);
  }, [changedRatio, channelTolerance, maskSelectors, selected, startUrl, validation.ok]);

  const handleCopy = async () => {
    if (!contractFragment) {
      setStatus(validation.message || '当前没有可复制的合同片段。');
      return;
    }
    try {
      await navigator.clipboard.writeText(contractFragment);
      setStatus('已复制可导航的 ui_request。正式来源合同仍需补齐 operation_ref、actor_role 与 source_refs。');
    } catch {
      outputRef.current?.focus();
      outputRef.current?.select();
      setStatus('浏览器未授权自动复制，已选中 ui_request，请手动复制。');
    }
  };

  return (
    <div className="section-card visual-contract-section">
      <div className="settings-card-head">
        <div>
          <span className="panel-kicker">来源合同绑定</span>
          <h2>视觉合同助手</h2>
          <p className="visual-contract-subtitle">
            从活动 registry 记录生成可导航的确定性 ui_request，避免手工抄写基线哈希、视口、页面地址和渲染档案。
            输出不会自动成为正式合同，企业资料仍必须提供真实 operation_ref、actor_role 与 source_refs。
          </p>
        </div>
        <button
          type="button"
          className="btn btn-secondary settings-btn-compact"
          disabled={!project || loading}
          onClick={() => void loadActiveBaselines(true)}
        >
          {loading ? '刷新中…' : '刷新基线'}
        </button>
      </div>

      <div className="visual-contract-grid">
        <div className="visual-contract-controls">
          <div className="form-group">
            <label className="form-label" htmlFor="visual-contract-baseline">活动基线</label>
            <select
              id="visual-contract-baseline"
              className="form-input"
              value={selectedId}
              disabled={!project || loading || records.length === 0}
              onChange={(event) => {
                setSelectedId(event.target.value);
                setStatus('');
              }}
            >
              {records.length === 0 && <option value="">暂无活动基线</option>}
              {records.map((record) => (
                <option key={record.baseline_id} value={record.baseline_id}>
                  {record.authority === 'approved_copy' ? '已审批' : '来源'} · {record.ref}
                </option>
              ))}
            </select>
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="visual-contract-start-url">被测页面路径 / URL</label>
            <input
              id="visual-contract-start-url"
              className="form-input"
              value={startUrl}
              disabled={!selected}
              maxLength={2000}
              placeholder="如：/orders 或 https://test.example.com/orders"
              onChange={(event) => {
                setStartUrl(event.target.value);
                setStatus('');
              }}
            />
            <span className="settings-hint">
              正式执行仍会按运行时 approved_base_url 校验同源，越权地址会被阻断。
            </span>
          </div>

          <div className="visual-contract-number-row">
            <div className="form-group">
              <label className="form-label" htmlFor="visual-change-ratio">允许变化比例</label>
              <input
                id="visual-change-ratio"
                className="form-input"
                type="number"
                min={0}
                max={1}
                step={0.0001}
                value={changedRatio}
                disabled={!selected}
                onChange={(event) => setChangedRatio(event.target.value)}
              />
              <span className="settings-hint">0.001 = 最多 0.1% 像素超出通道容差。</span>
            </div>
            <div className="form-group">
              <label className="form-label" htmlFor="visual-channel-tolerance">通道容差</label>
              <input
                id="visual-channel-tolerance"
                className="form-input"
                type="number"
                min={0}
                max={32}
                step={1}
                value={channelTolerance}
                disabled={!selected}
                onChange={(event) => setChannelTolerance(event.target.value)}
              />
              <span className="settings-hint">RGBA 任一通道差值大于该值才计为变化。</span>
            </div>
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="visual-mask-selectors">动态区域遮罩</label>
            <textarea
              id="visual-mask-selectors"
              className="form-input settings-textarea"
              rows={5}
              value={maskSelectors}
              disabled={!selected}
              placeholder={'每行一个来源声明的 CSS selector，例如：\n[data-testid="clock"]\n.live-order-count'}
              onChange={(event) => setMaskSelectors(event.target.value)}
            />
            <span className="settings-hint">最多 64 项。敏感输入控件仍会由执行器自动遮罩。</span>
          </div>

          {selected && (
            <div className="visual-contract-selected">
              <span><b>authority</b>{selected.authority === 'approved_copy' ? '已审批副本' : '来源登记'}</span>
              <span><b>视口</b>{selected.viewport_width} × {selected.viewport_height}</span>
              <span><b>截图</b>{selected.full_page ? '全页' : '当前视口'}</span>
              <span><b>基线 ID</b>{selected.baseline_id}</span>
            </div>
          )}
        </div>

        <div className="visual-contract-output">
          <div className="visual-contract-output-head">
            <div>
              <span>ui_request JSON</span>
              <small>包含显式导航和视觉比较，不包含页面像素。</small>
            </div>
            <button
              type="button"
              className="btn btn-primary settings-btn-compact"
              disabled={!contractFragment}
              onClick={handleCopy}
            >
              复制 ui_request
            </button>
          </div>
          <textarea
            ref={outputRef}
            className="visual-contract-code"
            readOnly
            spellCheck={false}
            aria-label="视觉基线 ui request JSON"
            value={contractFragment || (loading ? '正在读取活动基线…' : validation.message)}
          />
        </div>
      </div>

      {status && (
        <p className="settings-inline-feedback visual-contract-feedback" role="status" aria-live="polite">
          {status}
        </p>
      )}
    </div>
  );
}
