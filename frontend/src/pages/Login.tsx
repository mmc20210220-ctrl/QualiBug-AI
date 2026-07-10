import { type FormEvent, type MouseEvent, useMemo, useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { isAuthenticated, loginDetailed, register, resetPassword, type RegisterResult } from '../api/client';
import { usePageTitle } from '../lib/page-title';
import { BrandLogo } from '../components/BrandLogo';
import { LoginStageCanvas } from '../components/LoginStageCanvas';

type AuthMode = 'login' | 'register' | 'forgot';

function cleanNextPath(value: string | null): string {
  const next = String(value || '').trim();
  return !next || !next.startsWith('/') || next.startsWith('//') ? '' : next;
}

function initialMode(value: string | null): AuthMode {
  if (value === 'register') return 'register';
  if (value === 'forgot' || value === 'reset') return 'forgot';
  return 'login';
}

function humanizeAuthError(message: string, fallback: string): string {
  const text = message.trim();
  if (!text) return fallback;
  const lower = text.toLowerCase();
  if (lower.includes('unexpected end of json') || lower.includes("failed to execute 'json'")) {
    return '服务暂时不可用，请确认后端已启动后重试。';
  }
  if (lower.includes('failed to fetch') || lower.includes('networkerror') || lower.includes('load failed')) {
    return '无法连接服务，请确认后端已启动后重试。';
  }
  if (/^api\s+\d+/i.test(text) || /^http\s+\d+/i.test(text) || text.startsWith('SyntaxError')) {
    return fallback;
  }
  return text;
}

export function Login() {
  usePageTitle('登录');
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [mode, setMode] = useState<AuthMode>(initialMode(params.get('mode')));
  const [username, setUsername] = useState(params.get('username') || params.get('project') || '');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [workspaceName, setWorkspaceName] = useState(params.get('name') || '');
  const [workspaceId, setWorkspaceId] = useState(params.get('tenant') || '');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [pointer, setPointer] = useState({ x: 0.28, y: 0.48 });
  const [fieldFocused, setFieldFocused] = useState(false);
  const nextPath = useMemo(() => cleanNextPath(params.get('next')), [params]);

  if (isAuthenticated()) return <Navigate to={nextPath || '/settings'} replace />;

  const finishLogin = async (user: string, secret: string): Promise<void> => {
    const result = await loginDetailed(user, secret);
    if (!result?.token) throw new Error('登录失败，请确认账号凭证或联系系统管理员。');
    const fallback = `/settings?project=${encodeURIComponent(result.tenantId || user)}`;
    navigate(nextPath || fallback, { replace: true });
  };

  const handleLoginSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const user = username.trim();
    if (!user || !password) { setError('请输入账号和密码'); return; }
    setSubmitting(true); setError(''); setSuccess('');
    try { await finishLogin(user, password); }
    catch (caught: unknown) {
      setError(humanizeAuthError(caught instanceof Error ? caught.message : '', '登录失败，请稍后重试'));
    }
    finally { setSubmitting(false); }
  };

  const handleRegisterSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const id = workspaceId.trim(); const name = workspaceName.trim(); const user = username.trim();
    if (!id || !name || !user || !password) { setError('请填写完整注册信息'); return; }
    if (password !== confirmPassword) { setError('两次密码输入不一致'); return; }
    if (password.length < 8) { setError('密码长度至少 8 位'); return; }
    setSubmitting(true); setError(''); setSuccess('');
    try {
      const result: RegisterResult | null = await register({ tenantId: id, name, username: user, password });
      if (!result?.ok) throw new Error('创建工作区失败，请稍后重试');
      setSuccess('工作区已创建，正在进入...');
      await finishLogin(user, password);
    } catch (caught: unknown) {
      setError(humanizeAuthError(caught instanceof Error ? caught.message : '', '创建工作区失败，请稍后重试'));
      setSuccess('');
    } finally { setSubmitting(false); }
  };

  const handleForgotSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const id = workspaceId.trim();
    const user = username.trim();
    if (!id || !user || !password) { setError('请填写工作区、账号和新密码'); return; }
    if (password !== confirmPassword) { setError('两次密码输入不一致'); return; }
    if (password.length < 8) { setError('密码长度至少 8 位'); return; }
    setSubmitting(true); setError(''); setSuccess('');
    try {
      const result = await resetPassword({ tenantId: id, username: user, newPassword: password });
      if (!result?.ok) throw new Error('重置失败，请确认工作区与账号后重试');
      setSuccess('密码已重置，正在登录...');
      await finishLogin(user, password);
    } catch (caught: unknown) {
      setError(humanizeAuthError(caught instanceof Error ? caught.message : '', '重置失败，请确认工作区与账号后重试'));
      setSuccess('');
    } finally { setSubmitting(false); }
  };

  const switchTo = (next: AuthMode) => {
    setMode(next); setError(''); setSuccess(''); setConfirmPassword(''); setFieldFocused(false);
    if (next === 'login') setPassword('');
  };

  const onStagePointerMove = (event: MouseEvent<HTMLElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    setPointer({
      x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
    });
  };

  const onFieldFocus = () => setFieldFocused(true);
  const onFieldBlur = () => setFieldFocused(false);

  const panelKicker = mode === 'login' ? '进入工作区' : mode === 'register' ? '新建工作区' : '重置密码';
  const panelTitle = mode === 'login' ? '欢迎回来' : mode === 'register' ? '创建专属工作区' : '找回登录密码';
  const panelLead = mode === 'login'
    ? '登录后查看本轮业务结论与发布建议。'
    : mode === 'register'
      ? '创建后即可配置接入并开始真实检测。'
      : '用工作区 ID 与账号核验身份后，设置新密码。';

  return (
    <main className={`login-page${fieldFocused ? ' is-focused' : ''}${submitting ? ' is-submitting' : ''}${mode !== 'login' ? ' is-register' : ''}`} onMouseMove={onStagePointerMove}>
      <LoginStageCanvas pointerX={pointer.x} pointerY={pointer.y} focusBoost={fieldFocused || submitting} />
      <div className="login-stage-glow" />
      <div className="login-stage-scan" />
      <div className="login-stage-orb login-stage-orb-a" />
      <div className="login-stage-orb login-stage-orb-b" />
      <div className="login-light-bleed" aria-hidden="true" />
      <div className="login-hud-frame" aria-hidden="true">
        <i className="tl" /><i className="tr" /><i className="bl" /><i className="br" />
      </div>

      <aside className="login-stage">
        <div className="login-stage-brand">
          <BrandLogo variant="full" size={40} dark />
          <div className="login-live-badge">
            <span className="login-live-dot" />
            引擎在线
          </div>
        </div>

        <div
          className="login-stage-inner"
          style={{
            transform: `translate3d(${(pointer.x - 0.5) * -10}px, ${(pointer.y - 0.5) * -8}px, 0)`,
          }}
        >
          <h1 className="login-stage-title">
            <span className="login-title-line">上线前，先看清</span>
            <span className="login-title-accent">业务会不会出事</span>
          </h1>
          <p className="login-stage-lead">
            把软件风险变成可交付、可验收的业务结论。
          </p>
          <ul className="login-value-list">
            <li>
              <strong>发现真问题</strong>
              <span>只交付已验证的业务缺陷</span>
            </li>
            <li>
              <strong>结论有依据</strong>
              <span>说清影响、复现与验收</span>
            </li>
            <li>
              <strong>发布有把握</strong>
              <span>回答现在能不能上线</span>
            </li>
          </ul>
        </div>
      </aside>

      <section className="login-panel">
        <div className="login-panel-glow" aria-hidden="true" />
        <div className="login-panel-inner" key={mode}>
          <div className="login-brand login-brand-mobile"><BrandLogo variant="full" size={40} dark /></div>
          <div className="login-copy">
            <span className="panel-kicker">{panelKicker}</span>
            <h1>{panelTitle}</h1>
            <p>{panelLead}</p>
          </div>

          {mode === 'login' ? (
            <form className="login-form" onSubmit={handleLoginSubmit}>
              <label>
                <span>账号</span>
                <input
                  className="form-input"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  onFocus={onFieldFocus}
                  onBlur={onFieldBlur}
                  placeholder="输入已分配账号"
                  autoComplete="username"
                />
              </label>
              <label>
                <span className="login-label-row">
                  密码
                  <button type="button" className="btn-link login-forgot-link" onClick={() => switchTo('forgot')}>
                    忘记密码
                  </button>
                </span>
                <input
                  className="form-input"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  onFocus={onFieldFocus}
                  onBlur={onFieldBlur}
                  placeholder="输入密码"
                  type="password"
                  autoComplete="current-password"
                />
              </label>
              {error && <div className="login-error" role="alert">{error}</div>}
              {success && <div className="login-success">{success}</div>}
              <button className={`btn btn-primary login-submit${submitting ? ' is-loading' : ''}`} type="submit" disabled={submitting}>
                <span>{submitting ? '登录中...' : '进入成果台'}</span>
              </button>
            </form>
          ) : mode === 'register' ? (
            <form className="login-form login-form-register" onSubmit={handleRegisterSubmit}>
              <div className="login-form-row">
                <label>
                  <span>工作区 ID</span>
                  <input
                    className="form-input"
                    value={workspaceId}
                    onChange={(event) => setWorkspaceId(event.target.value)}
                    onFocus={onFieldFocus}
                    onBlur={onFieldBlur}
                    placeholder="例如 acme-prod"
                    autoComplete="off"
                  />
                </label>
                <label>
                  <span>工作区名称</span>
                  <input
                    className="form-input"
                    value={workspaceName}
                    onChange={(event) => setWorkspaceName(event.target.value)}
                    onFocus={onFieldFocus}
                    onBlur={onFieldBlur}
                    placeholder="例如 验收项目"
                    autoComplete="organization"
                  />
                </label>
              </div>
              <label>
                <span>登录账号</span>
                <input
                  className="form-input"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  onFocus={onFieldFocus}
                  onBlur={onFieldBlur}
                  placeholder="用于登录的账号"
                  autoComplete="username"
                />
              </label>
              <div className="login-form-row">
                <label>
                  <span>密码</span>
                  <input
                    className="form-input"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    onFocus={onFieldFocus}
                    onBlur={onFieldBlur}
                    placeholder="至少 8 位"
                    type="password"
                    autoComplete="new-password"
                  />
                </label>
                <label>
                  <span>确认密码</span>
                  <input
                    className="form-input"
                    value={confirmPassword}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                    onFocus={onFieldFocus}
                    onBlur={onFieldBlur}
                    placeholder="再次输入"
                    type="password"
                    autoComplete="new-password"
                  />
                </label>
              </div>
              {error && <div className="login-error" role="alert">{error}</div>}
              {success && <div className="login-success">{success}</div>}
              <button className={`btn btn-primary login-submit${submitting ? ' is-loading' : ''}`} type="submit" disabled={submitting}>
                <span>{submitting ? '创建中...' : '创建并进入'}</span>
              </button>
            </form>
          ) : (
            <form className="login-form login-form-register" onSubmit={handleForgotSubmit}>
              <label>
                <span>工作区 ID</span>
                <input
                  className="form-input"
                  value={workspaceId}
                  onChange={(event) => setWorkspaceId(event.target.value)}
                  onFocus={onFieldFocus}
                  onBlur={onFieldBlur}
                  placeholder="创建时填写的工作区 ID"
                  autoComplete="off"
                />
              </label>
              <label>
                <span>登录账号</span>
                <input
                  className="form-input"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  onFocus={onFieldFocus}
                  onBlur={onFieldBlur}
                  placeholder="用于登录的账号"
                  autoComplete="username"
                />
              </label>
              <div className="login-form-row">
                <label>
                  <span>新密码</span>
                  <input
                    className="form-input"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    onFocus={onFieldFocus}
                    onBlur={onFieldBlur}
                    placeholder="至少 8 位"
                    type="password"
                    autoComplete="new-password"
                  />
                </label>
                <label>
                  <span>确认新密码</span>
                  <input
                    className="form-input"
                    value={confirmPassword}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                    onFocus={onFieldFocus}
                    onBlur={onFieldBlur}
                    placeholder="再次输入"
                    type="password"
                    autoComplete="new-password"
                  />
                </label>
              </div>
              {error && <div className="login-error" role="alert">{error}</div>}
              {success && <div className="login-success">{success}</div>}
              <button className={`btn btn-primary login-submit${submitting ? ' is-loading' : ''}`} type="submit" disabled={submitting}>
                <span>{submitting ? '重置中...' : '重置并登录'}</span>
              </button>
            </form>
          )}

          <div className="login-switch">
            {mode === 'login' ? (
              <>
                <span>还没有工作区？</span>
                <button type="button" className="btn-link login-switch-btn" onClick={() => switchTo('register')}>立即创建</button>
              </>
            ) : (
              <>
                <span>{mode === 'forgot' ? '想起密码了？' : '已经有账号？'}</span>
                <button type="button" className="btn-link login-switch-btn" onClick={() => switchTo('login')}>返回登录</button>
              </>
            )}
          </div>
          <p className="login-trust-note">只交付站得住的业务结论</p>
        </div>
      </section>
    </main>
  );
}

export default Login;
