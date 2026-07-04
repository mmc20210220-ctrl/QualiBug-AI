import { FormEvent, useMemo, useState } from 'react';
import { Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { isAuthenticated, loginDetailed } from '../api/client';
import { usePageTitle } from '../lib/page-title';

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
  const [username, setUsername] = useState(params.get('username') || params.get('project') || '');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const nextPath = useMemo(() => cleanNextPath(params.get('next')), [params]);

  if (isAuthenticated()) {
    return <Navigate to={nextPath || '/settings'} replace />;
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const user = username.trim();
    if (!user || !password) {
      setError('请输入用户名和密码');
      return;
    }

    setSubmitting(true);
    setError('');
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

  return (
    <main className="login-page">
      <section className="login-panel">
        <div className="login-brand">
          <span className="login-brand-mark">QB</span>
          <div>
            <strong>QualiBug AI</strong>
            <span>Enterprise Quality Command Center</span>
          </div>
        </div>
        <div className="login-copy">
          <span className="panel-kicker">客户工作台</span>
          <h1>登录后进入当前客户项目</h1>
          <p>使用客户账号访问专属的资料、服务接入、扫描结果和交付证据。</p>
        </div>
        <form className="login-form" onSubmit={handleSubmit}>
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
          <button className="btn btn-primary login-submit" type="submit" disabled={submitting}>
            {submitting ? '登录中...' : '登录'}
          </button>
        </form>
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
