import { type FormEvent, useMemo, useState } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { isAuthenticated, loginDetailed, register, type RegisterResult } from '../api/client';
import { usePageTitle } from '../lib/page-title';
import { BrandLogo } from '../components/BrandLogo';

function cleanNextPath(value: string | null): string {
  const next = String(value || '').trim();
  return !next || !next.startsWith('/') || next.startsWith('//') ? '' : next;
}

export function Login() {
  usePageTitle('登录');
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [mode, setMode] = useState<'login' | 'register'>(params.get('mode') === 'register' ? 'register' : 'login');
  const [username, setUsername] = useState(params.get('username') || params.get('project') || '');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [workspaceName, setWorkspaceName] = useState(params.get('name') || '');
  const [workspaceId, setWorkspaceId] = useState(params.get('tenant') || '');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [submitting, setSubmitting] = useState(false);
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
    catch (caught: unknown) { setError(caught instanceof Error ? caught.message : '登录失败，请稍后重试'); }
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
      if (!result?.ok) throw new Error('注册失败，请稍后重试');
      setSuccess('注册成功，正在登录...');
      await finishLogin(user, password);
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : '注册失败，请稍后重试');
      setSuccess('');
    } finally { setSubmitting(false); }
  };

  const switchTo = (next: 'login' | 'register') => {
    setMode(next); setError(''); setSuccess(''); setConfirmPassword('');
    if (next === 'register' && !workspaceId) setWorkspaceId(username);
  };

  return (
    <main className="login-page">
      <section className="login-panel">
        <div className="login-brand"><BrandLogo variant="full" size={48} /></div>
        <div className="login-copy">
          <span className="panel-kicker">{mode === 'login' ? '工作台登录' : '新建工作区'}</span>
          <h1>{mode === 'login' ? '登录后进入授权工作区' : '创建隔离的工作区与初始账号'}</h1>
          <p>{mode === 'login' ? '使用已分配账号访问资料、服务接入、扫描结果和证据。' : '创建工作区后，系统会按管理员配置的认证与权限策略完成初始化。'}</p>
        </div>
        {mode === 'login' ? (
          <form className="login-form" onSubmit={handleLoginSubmit}>
            <label><span>账号</span><input className="form-input" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="输入已分配账号" autoComplete="username" /></label>
            <label><span>密码</span><input className="form-input" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="输入密码" type="password" autoComplete="current-password" /></label>
            {error && <div className="login-error">{error}</div>}{success && <div className="login-success">{success}</div>}
            <button className="btn btn-primary login-submit" type="submit" disabled={submitting}>{submitting ? '登录中...' : '登录'}</button>
          </form>
        ) : (
          <form className="login-form" onSubmit={handleRegisterSubmit}>
            <label><span>工作区 ID</span><input className="form-input" value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} placeholder="输入唯一工作区标识" autoComplete="off" /></label>
            <label><span>工作区名称</span><input className="form-input" value={workspaceName} onChange={(event) => setWorkspaceName(event.target.value)} placeholder="输入工作区名称" autoComplete="organization" /></label>
            <label><span>初始账号</span><input className="form-input" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="输入初始账号" autoComplete="username" /></label>
            <label><span>密码</span><input className="form-input" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="至少 8 位" type="password" autoComplete="new-password" /></label>
            <label><span>确认密码</span><input className="form-input" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} placeholder="再次输入密码" type="password" autoComplete="new-password" /></label>
            {error && <div className="login-error">{error}</div>}{success && <div className="login-success">{success}</div>}
            <button className="btn btn-primary login-submit" type="submit" disabled={submitting}>{submitting ? '注册中...' : '创建工作区'}</button>
          </form>
        )}
        <div className="login-switch">{mode === 'login' ? <><span>还没有账号？</span><button type="button" className="btn-link login-switch-btn" onClick={() => switchTo('register')}>创建工作区</button></> : <><span>已经有账号？</span><button type="button" className="btn-link login-switch-btn" onClick={() => switchTo('login')}>返回登录</button></>}</div>
      </section>
      <aside className="login-proof">
        <div className="login-proof-item"><span>01</span><strong>工作区隔离</strong><p>登录身份决定可访问的项目与资料范围。</p></div>
        <div className="login-proof-item"><span>02</span><strong>可配置接入</strong><p>认证、服务凭证和扫描范围由工作区配置管理。</p></div>
        <div className="login-proof-item"><span>03</span><strong>可审计</strong><p>每次接入和检测结果均可追溯到授权工作区。</p></div>
      </aside>
    </main>
  );
}

export default Login;
