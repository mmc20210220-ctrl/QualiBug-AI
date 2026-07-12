import {
  type ChangeEventHandler,
  type FocusEventHandler,
  type FormEvent,
  type MouseEvent,
  useMemo,
  useState,
} from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { isAuthenticated, loginDetailed, register, resetPassword, type RegisterResult } from '../api/client';
import { BrandLogo } from '../components/BrandLogo';
import { LoginStageCanvas } from '../components/LoginStageCanvas';
import { PasswordField } from '../components/auth/PasswordField';
import { ServiceHealthBadge } from '../components/auth/ServiceHealthBadge';
import { usePageTitle } from '../lib/page-title';

type AuthMode = 'login' | 'register' | 'forgot';

type TextFieldProps = {
  id: string;
  name: string;
  label: string;
  value: string;
  onChange: ChangeEventHandler<HTMLInputElement>;
  onFocus: FocusEventHandler<HTMLInputElement>;
  onBlur: FocusEventHandler<HTMLInputElement>;
  placeholder: string;
  autoComplete: string;
  invalid: boolean;
  describedBy?: string;
};

function TextField({
  id,
  name,
  label,
  value,
  onChange,
  onFocus,
  onBlur,
  placeholder,
  autoComplete,
  invalid,
  describedBy,
}: TextFieldProps) {
  return (
    <div className="login-field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        name={name}
        className="form-input"
        value={value}
        onChange={onChange}
        onFocus={onFocus}
        onBlur={onBlur}
        placeholder={placeholder}
        autoComplete={autoComplete}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
      />
    </div>
  );
}

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
    if (!user || !password) {
      setError('请输入账号和密码');
      return;
    }
    setSubmitting(true);
    setError('');
    setSuccess('');
    try {
      await finishLogin(user, password);
    } catch (caught: unknown) {
      setError(humanizeAuthError(caught instanceof Error ? caught.message : '', '登录失败，请稍后重试'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleRegisterSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const id = workspaceId.trim();
    const name = workspaceName.trim();
    const user = username.trim();
    if (!id || !name || !user || !password) {
      setError('请填写完整注册信息');
      return;
    }
    if (password !== confirmPassword) {
      setError('两次密码输入不一致');
      return;
    }
    if (password.length < 8) {
      setError('密码长度至少 8 位');
      return;
    }
    setSubmitting(true);
    setError('');
    setSuccess('');
    try {
      const result: RegisterResult | null = await register({ tenantId: id, name, username: user, password });
      if (!result?.ok) throw new Error('创建工作区失败，请稍后重试');
      setSuccess('工作区已创建，正在进入...');
      await finishLogin(user, password);
    } catch (caught: unknown) {
      setError(humanizeAuthError(caught instanceof Error ? caught.message : '', '创建工作区失败，请稍后重试'));
      setSuccess('');
    } finally {
      setSubmitting(false);
    }
  };

  const handleForgotSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const id = workspaceId.trim();
    const user = username.trim();
    if (!id || !user || !password) {
      setError('请填写工作区、账号和新密码');
      return;
    }
    if (password !== confirmPassword) {
      setError('两次密码输入不一致');
      return;
    }
    if (password.length < 8) {
      setError('密码长度至少 8 位');
      return;
    }
    setSubmitting(true);
    setError('');
    setSuccess('');
    try {
      const result = await resetPassword({ tenantId: id, username: user, newPassword: password });
      if (!result?.ok) throw new Error('重置失败，请确认工作区与账号后重试');
      setSuccess('密码已重置，正在登录...');
      await finishLogin(user, password);
    } catch (caught: unknown) {
      setError(humanizeAuthError(caught instanceof Error ? caught.message : '', '重置失败，请确认工作区与账号后重试'));
      setSuccess('');
    } finally {
      setSubmitting(false);
    }
  };

  const switchTo = (next: AuthMode) => {
    setMode(next);
    setError('');
    setSuccess('');
    setConfirmPassword('');
    setPassword('');
    setFieldFocused(false);
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
  const errorId = error ? 'auth-form-error' : undefined;
  const invalid = Boolean(error);

  const panelKicker = mode === 'login' ? 'WELCOME BACK' : mode === 'register' ? 'CREATE WORKSPACE' : 'RESET ACCESS';
  const panelTitle = mode === 'login' ? '登录工作区' : mode === 'register' ? '创建工作区' : '重置登录密码';
  const panelLead = mode === 'login'
    ? '查看本轮业务结论与发布建议。'
    : mode === 'register'
      ? '创建后即可配置接入并开始真实检测。'
      : '核验工作区与账号后，设置新的登录密码。';

  const feedback = (
    <>
      {error && <div id="auth-form-error" className="login-error" role="alert">{error}</div>}
      {success && <div className="login-success" role="status" aria-live="polite">{success}</div>}
    </>
  );

  return (
    <main
      className={`login-page${fieldFocused ? ' is-focused' : ''}${submitting ? ' is-submitting' : ''}${mode !== 'login' ? ' is-register' : ''}`}
      onMouseMove={onStagePointerMove}
    >
      <LoginStageCanvas pointerX={pointer.x} pointerY={pointer.y} focusBoost={fieldFocused || submitting} />
      <div className="login-stage-glow" aria-hidden="true" />
      <div className="login-stage-scan" aria-hidden="true" />
      <div className="login-stage-orb login-stage-orb-a" aria-hidden="true" />
      <div className="login-stage-orb login-stage-orb-b" aria-hidden="true" />
      <div className="login-light-bleed" aria-hidden="true" />
      <div className="login-hud-frame" aria-hidden="true">
        <i className="tl" /><i className="tr" /><i className="bl" /><i className="br" />
      </div>

      <aside className="login-stage" aria-label="QualiBug AI 产品价值">
        <div className="login-stage-brand">
          <BrandLogo variant="full" detail="compact" tone="dark" size={40} />
          <ServiceHealthBadge />
        </div>

        <div
          className="login-stage-inner"
          style={{
            transform: `translate3d(${(pointer.x - 0.5) * -7}px, ${(pointer.y - 0.5) * -5}px, 0)`,
          }}
        >
          <span className="login-stage-kicker">EVIDENCE-DRIVEN QUALITY</span>
          <h1 className="login-stage-title">
            <span className="login-title-line">上线前，先看清</span>
            <span className="login-title-accent">业务会不会出事</span>
          </h1>
          <p className="login-stage-lead">把软件风险变成可复现、可验收、可决策的业务结论。</p>
          <ul className="login-value-list">
            <li><strong>发现真问题</strong><span>验证后再交付</span></li>
            <li><strong>结论有证据</strong><span>影响与复现可追溯</span></li>
            <li><strong>发布有依据</strong><span>风险门禁清晰可见</span></li>
          </ul>
        </div>
      </aside>

      <section className="login-panel" aria-label="身份验证">
        <div className="login-panel-glow" aria-hidden="true" />
        <div className="login-panel-inner" key={mode}>
          <div className="login-copy">
            <span className="panel-kicker">{panelKicker}</span>
            <h1>{panelTitle}</h1>
            <p>{panelLead}</p>
          </div>

          {mode === 'login' ? (
            <form className="login-form" onSubmit={handleLoginSubmit} noValidate>
              <TextField
                id="login-username"
                name="username"
                label="账号"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                onFocus={onFieldFocus}
                onBlur={onFieldBlur}
                placeholder="输入已分配账号"
                autoComplete="username"
                invalid={invalid}
                describedBy={errorId}
              />
              <PasswordField
                id="login-password"
                name="password"
                label="密码"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                onFocus={onFieldFocus}
                onBlur={onFieldBlur}
                placeholder="输入密码"
                autoComplete="current-password"
                invalid={invalid}
                describedBy={errorId}
                action={(
                  <button
                    type="button"
                    className="btn-link login-forgot-link"
                    aria-label="忘记密码？"
                    onClick={() => switchTo('forgot')}
                  >
                    忘记密码？
                  </button>
                )}
              />
              {feedback}
              <button className={`btn btn-primary login-submit${submitting ? ' is-loading' : ''}`} type="submit" disabled={submitting}>
                <span>{submitting ? '安全登录中...' : '安全登录'}</span>
                {!submitting && <span className="login-submit-arrow" aria-hidden="true">→</span>}
              </button>
            </form>
          ) : mode === 'register' ? (
            <form className="login-form login-form-register" onSubmit={handleRegisterSubmit} noValidate>
              <div className="login-form-row">
                <TextField
                  id="register-workspace-id"
                  name="workspaceId"
                  label="工作区 ID"
                  value={workspaceId}
                  onChange={(event) => setWorkspaceId(event.target.value)}
                  onFocus={onFieldFocus}
                  onBlur={onFieldBlur}
                  placeholder="例如 acme-qa"
                  autoComplete="off"
                  invalid={invalid}
                  describedBy={errorId}
                />
                <TextField
                  id="register-workspace-name"
                  name="workspaceName"
                  label="工作区名称"
                  value={workspaceName}
                  onChange={(event) => setWorkspaceName(event.target.value)}
                  onFocus={onFieldFocus}
                  onBlur={onFieldBlur}
                  placeholder="例如 软件验收"
                  autoComplete="organization"
                  invalid={invalid}
                  describedBy={errorId}
                />
              </div>
              <TextField
                id="register-username"
                name="username"
                label="登录账号"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                onFocus={onFieldFocus}
                onBlur={onFieldBlur}
                placeholder="用于登录的账号"
                autoComplete="username"
                invalid={invalid}
                describedBy={errorId}
              />
              <div className="login-form-row">
                <PasswordField
                  id="register-password"
                  name="password"
                  label="密码"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  onFocus={onFieldFocus}
                  onBlur={onFieldBlur}
                  placeholder="至少 8 位"
                  autoComplete="new-password"
                  invalid={invalid}
                  describedBy={errorId}
                />
                <PasswordField
                  id="register-confirm-password"
                  name="confirmPassword"
                  label="确认密码"
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  onFocus={onFieldFocus}
                  onBlur={onFieldBlur}
                  placeholder="再次输入"
                  autoComplete="new-password"
                  invalid={invalid}
                  describedBy={errorId}
                />
              </div>
              {feedback}
              <button className={`btn btn-primary login-submit${submitting ? ' is-loading' : ''}`} type="submit" disabled={submitting}>
                <span>{submitting ? '创建中...' : '创建并进入工作区'}</span>
                {!submitting && <span className="login-submit-arrow" aria-hidden="true">→</span>}
              </button>
            </form>
          ) : (
            <form className="login-form login-form-register" onSubmit={handleForgotSubmit} noValidate>
              <TextField
                id="reset-workspace-id"
                name="workspaceId"
                label="工作区 ID"
                value={workspaceId}
                onChange={(event) => setWorkspaceId(event.target.value)}
                onFocus={onFieldFocus}
                onBlur={onFieldBlur}
                placeholder="创建时填写的工作区 ID"
                autoComplete="off"
                invalid={invalid}
                describedBy={errorId}
              />
              <TextField
                id="reset-username"
                name="username"
                label="登录账号"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                onFocus={onFieldFocus}
                onBlur={onFieldBlur}
                placeholder="用于登录的账号"
                autoComplete="username"
                invalid={invalid}
                describedBy={errorId}
              />
              <div className="login-form-row">
                <PasswordField
                  id="reset-password"
                  name="password"
                  label="新密码"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  onFocus={onFieldFocus}
                  onBlur={onFieldBlur}
                  placeholder="至少 8 位"
                  autoComplete="new-password"
                  invalid={invalid}
                  describedBy={errorId}
                />
                <PasswordField
                  id="reset-confirm-password"
                  name="confirmPassword"
                  label="确认新密码"
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  onFocus={onFieldFocus}
                  onBlur={onFieldBlur}
                  placeholder="再次输入"
                  autoComplete="new-password"
                  invalid={invalid}
                  describedBy={errorId}
                />
              </div>
              {feedback}
              <button className={`btn btn-primary login-submit${submitting ? ' is-loading' : ''}`} type="submit" disabled={submitting}>
                <span>{submitting ? '重置中...' : '重置密码并登录'}</span>
                {!submitting && <span className="login-submit-arrow" aria-hidden="true">→</span>}
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
          <p className="login-trust-note">身份信息通过安全连接提交 · 不在页面保存密码</p>
        </div>
      </section>
    </main>
  );
}

export default Login;
