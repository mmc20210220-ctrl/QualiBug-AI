import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate, Outlet, useLocation } from 'react-router-dom';
import { Layout } from './components/Layout';
import { ToastProvider } from './components/Toast';
import { ScrollToTop } from './components/ScrollToTop';
import { AuthProvider } from './components/AuthProvider';
import { useAuth } from './components/useAuth';

const AgentHome = lazy(() => import('./pages/AgentHome'));
const AgentFindings = lazy(() => import('./pages/AgentFindings'));
const AgentDecision = lazy(() => import('./pages/AgentDecision'));
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Analyze = lazy(() => import('./pages/Analyze'));
const Verify = lazy(() => import('./pages/Verify'));
const RequirementIntelligence = lazy(() => import('./pages/RequirementIntelligence').then(({ RequirementIntelligence: page }) => ({ default: page })));
const TestIntelligence = lazy(() => import('./pages/TestIntelligence').then(({ TestIntelligence: page }) => ({ default: page })));
const Findings = lazy(() => import('./pages/Findings'));
const EvidenceChain = lazy(() => import('./pages/EvidenceChain'));
const ReleaseGate = lazy(() => import('./pages/ReleaseGate'));
const EnterpriseCampaigns = lazy(() => import('./pages/EnterpriseCampaigns'));
const CoverageMatrix = lazy(() => import('./pages/CoverageMatrix'));
const SystemJobs = lazy(() => import('./pages/SystemJobs').then(({ SystemJobs: page }) => ({ default: page })));
const Materials = lazy(() => import('./pages/Materials'));
const Settings = lazy(() => import('./pages/Settings'));
const Integration = lazy(() => import('./pages/Integration'));
const FindingDetail = lazy(() => import('./pages/FindingDetail'));
const Login = lazy(() => import('./pages/Login'));
const SharedEvidence = lazy(() => import('./pages/SharedEvidence'));

function RequireAuth() {
  const location = useLocation();
  const { status, error, refresh } = useAuth();

  if (status === 'checking') {
    return (
      <div className="auth-gate" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <p>正在校验登录状态…</p>
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div className="auth-gate auth-gate-error" role="alert">
        <h1>服务暂时不可用</h1>
        <p>{error || '无法连接后端服务，请确认后端已启动后重试。'}</p>
        <button type="button" className="btn btn-primary" onClick={() => void refresh()}>重新连接</button>
      </div>
    );
  }

  if (status === 'unauthenticated') {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }

  return <Outlet />;
}

function PreserveSearchRedirect({ to }: { to: string }) {
  const location = useLocation();
  return <Navigate to={`${to}${location.search}`} replace />;
}

export default function App() {
  return (
    <ToastProvider>
      <AuthProvider>
        <BrowserRouter>
          <ScrollToTop />
          <Suspense fallback={(
            <div className="auth-gate" role="status" aria-live="polite">
              <span className="spinner" aria-hidden="true" />
              <p>正在加载工作区…</p>
            </div>
          )}>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/shared-evidence" element={<SharedEvidence />} />
              <Route element={<RequireAuth />}>
                <Route element={<Layout />}>
                  <Route path="/" element={<PreserveSearchRedirect to="/dashboard" />} />

                  {/* Agent-first 主链：New Task → Live Workspace → Findings → Decision。 */}
                  <Route path="/dashboard" element={<AgentHome />} />
                  <Route path="/verify" element={<Verify />} />
                  <Route path="/findings" element={<AgentFindings />} />
                  <Route path="/findings/:id" element={<FindingDetail />} />
                  <Route path="/release" element={<AgentDecision />} />

                  {/* Knowledge / Understanding 是 Agent 能力，不再作为客户第一层流水线导航。 */}
                  <Route path="/analyze" element={<Analyze />} />
                  <Route path="/integration" element={<Integration />} />
                  <Route path="/settings" element={<Settings />} />

                  {/* 兼容与高级页面：保留既有能力和 URL，不复制第二套数据模型。 */}
                  <Route path="/advanced-dashboard" element={<Dashboard />} />
                  <Route path="/advanced-findings" element={<Findings />} />
                  <Route path="/release/details" element={<ReleaseGate />} />
                  <Route path="/requirements" element={<RequirementIntelligence />} />
                  <Route path="/test-intelligence" element={<TestIntelligence />} />
                  <Route path="/evidence" element={<EvidenceChain />} />
                  <Route path="/materials" element={<Materials />} />
                  <Route path="/jobs" element={<SystemJobs />} />
                  <Route path="/campaigns" element={<EnterpriseCampaigns />} />
                  <Route path="/coverage" element={<CoverageMatrix />} />
                  <Route path="/behavior-space" element={<PreserveSearchRedirect to="/coverage" />} />
                  <Route path="/test-tasks" element={<PreserveSearchRedirect to="/campaigns" />} />
                  <Route path="/clues" element={<PreserveSearchRedirect to="/settings" />} />
                  <Route path="/products" element={<PreserveSearchRedirect to="/analyze" />} />
                  <Route path="*" element={<PreserveSearchRedirect to="/dashboard" />} />
                </Route>
              </Route>
            </Routes>
          </Suspense>
        </BrowserRouter>
      </AuthProvider>
    </ToastProvider>
  );
}
