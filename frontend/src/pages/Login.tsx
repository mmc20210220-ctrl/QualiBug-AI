import { FormEvent, useMemo, useState } from 'react';
import { Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { isAuthenticated, loginDetailed, register, type RegisterResult } from '../api/client';
import { usePageTitle } from '../lib/page-title';
import { BrandLogo } from '../components/BrandLogo';

function cleanNextPath(value: string | null) {
  const next = String(value || '').trim();
  if (!next || !next.startsWith('/') || next.startsWith('//')) return '';
  return next;
}

export function Login() {
  usePageTitle('登录');
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const [mode, setMode] = useState<'login' | 'register'>(params.get('mode') === 'register' ? 'register' : 'login');
  const [username, setUsername] = useState(params.get('username') || params.get('project') || '');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [tenantName, setTenantName] = useState(params.get('name') || '');
  const [tenantId, setTenantId] = useState(params.get('tenant') || '');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const nextPath = useMemo(() => cleanNextPath(params.get('next')), [params]);

  if (isAuthenticated()) {
    return <Navigate to={nextPath || '/settings'} replace />;
  }

  const handleLoginSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const user = username.trim();
    if (!user || !password) {
      setError('请输入用户名和密码');
      return;
    }

    setSubmitting(true);
    setError('');
    setSuccess('');
    try {
      const result = await loginDetailed(user, password);
      if (!result?.token) {
        setError('用户名或密码不正确');
        return;
      }
      const fallback = `/settings?project=${encodeURIComponent(result.tenantId || user)}`;
      navigate(nextPath || fallback, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  const handleRegisterSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const tid = tenantId.trim();
    const name = tenantName.trim();
    const user = username.trim();
    if (!tid || !name || !user || !password) {
      setError('请填写完整注册信息');
      return;
    }
    if (password !== confirmPassword) {
      setError('两次密码输入不一致');
      return;
    }
    if (password.length < 4) {
      setError('密码长度至少4位');
      return;
    }

    setSubmitting(true);
    setError('');
    setSuccess('');
    try {
      const result: RegisterResult | null = await register({
        tenantId: tid,
        name: name,
        username: user,
        password: password,
        role: 'admin',
      });
      if (!result?.ok) {
        setError('注册失败，请稍后重试');
        return;
      }
      setSuccess('注册成功！正在自动登录...');
      setTimeout(async () => {
        try {
          const loginResult = await loginDetailed(user, password);
          if (!loginResult?.token) {
            setMode('login');
            setSuccess('');
            return;
          }
          const fallback = `/settings?project=${encodeURIComponent(loginResult.tenantId || user)}`;
          navigate(nextPath || fallback, { replace: true });
        } catch {
          setMode('login');
          setSuccess('');
        }
      }, 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : '注册失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  const switchTo = (next: 'login' | 'register') => {
    setMode(next);
    setError('');
    setSuccess('');
    setConfirmPassword('');
    if (next === 'register' && !tenantId) {
      setTenantId(username);
    }
  };

  return (
    <main className="login-page">
      <section className="login-panel">
        <div className="login-brand">
          <BrandLogo variant="full" size={48} />
        </div>
        <div className="login-copy">
          <span className="panel-kicker">{mode === 'login' ? '客户工作台' : '新客户注册'}</span>
          <h1>{mode === 'login' ? '登录后进入当前客户项目' : '创建新的客户项目'}</h1>
          <p>{mode === 'login'
            ? '使用客户账号访问专属的资料、服务接入、扫描结果和交付证据。'
            : '创建新的客户租户，自动配置独立项目空间。'}</p>
        </div>
        {mode === 'login' ? (
          <form className="login-form" onSubmit={handleLoginSubmit}>
            <label>
              <span>用户名</span>
              <input
                className="form-input"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="例如：第一个真实项目测试"
                autoComplete="username"
              />
            </label>
            <label>
              <span>密码</span>
              <input
                className="form-input"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="客户创建时生成的密码"
                type="password"
                autoComplete="current-password"
              />
            </label>
            {error && <div className="login-error">{error}</div>}
            {success && <div className="login-success">{success}</div>}
            <button className="btn btn-primary login-submit" type="submit" disabled={submitting}>
              {submitting ? '登录中...' : '登录'}
            </button>
          </form>
        ) : (
          <form className="login-form" onSubmit={handleRegisterSubmit}>
            <label>
              <span>客户 ID</span>
              <input
                className="form-input"
                value={tenantId}
                onChange={(event) => setTenantId(event.target.value)}
                placeholder="例如：demo-customer"
                autoComplete="off"
              />
            </label>
            <label>
              <span>客户名称</span>
              <input
                className="form-input"
                value={tenantName}
                onChange={(event) => setTenantName(event.target.value)}
                placeholder="例如：演示客户"
                autoComplete="off"
              />
            </label>
            <label>
              <span>管理员账号</span>
              <input
                className="form-input"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                placeholder="例如：admin"
                autoComplete="username"
              />
            </label>
            <label>
              <span>密码</span>
              <input
                className="form-input"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="至少4位"
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
                placeholder="再次输入密码"
                type="password"
                autoComplete="new-password"
              />
            </label>
            {error && <div className="login-error">{error}</div>}
            {success && <div className="login-success">{success}</div>}
            <button className="btn btn-primary login-submit" type="submit" disabled={submitting}>
              {submitting ? '注册中...' : '注册'}
            </button>
          </form>
        )}
        <div className="login-switch">
          {mode === 'login' ? (
            <>
              <span>还没有账号？</span>
              <button type="button" className="btn-link login-switch-btn" onClick={() => switchTo('register')}>
                现在注册
              </button>
            </>
          ) : (
            <>
              <span>已经有账号？</span>
              <button type="button" className="btn-link login-switch-btn" onClick={() => switchTo('login')}>
                返回登录
              </button>
            </>
          )}
        </div>
        <div className="login-footnote">
          当前路径：<code>{location.pathname}{location.search}</code>
        </div>
      </section>
      <aside className="login-proof">
        <div className="login-proof-item">
          <span>01</span>
          <strong>租户隔离</strong>
          <p>登录身份决定客户列表和项目访问范围。</p>
        </div>
        <div className="login-proof-item">
          <span>02</span>
          <strong>现场交付</strong>
          <p>客户资料、凭证配置、扫描证据进入同一个闭环。</p>
        </div>
        <div className="login-proof-item">
          <span>03</span>
          <strong>可审计</strong>
          <p>每次接入和检测结果都绑定到明确客户。</p>
        </div>
      </aside>
    </main>
  );
}

export default Login;
