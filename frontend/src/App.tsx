import { BrowserRouter, Routes, Route, Navigate, Outlet, useLocation } from 'react-router-dom';
import { Layout } from './components/Layout';
import { ToastProvider } from './components/Toast';
import { ScrollToTop } from './components/ScrollToTop';
import { AuthProvider } from './components/AuthProvider';
import { useAuth } from './components/useAuth';
import { Dashboard } from './pages/Dashboard';
import { Analyze } from './pages/Analyze';
import { Verify } from './pages/Verify';
import { RequirementIntelligence } from './pages/RequirementIntelligence';
import { TestIntelligence } from './pages/TestIntelligence';
import { Findings } from './pages/Findings';
import { EvidenceChain } from './pages/EvidenceChain';
import { ReleaseGate } from './pages/ReleaseGate';
import { EnterpriseCampaigns } from './pages/EnterpriseCampaigns';
import { CoverageMatrix } from './pages/CoverageMatrix';
import { SystemJobs } from './pages/SystemJobs';
import { Materials } from './pages/Materials';
import { Settings } from './pages/Settings';
import { Integration } from './pages/Integration';
import { FindingDetail } from './pages/FindingDetail';
import { Login } from './pages/Login';
import { SharedEvidence } from './pages/SharedEvidence';

function RequireAuth() {
  const location = useLocation();
  const { status, error, refresh } = useAuth();

  // 校验中：先展示可感知的加载态，避免把「还没校验完」误判成未登录并跳登录页。
  if (status === 'checking') {
    return (
      <div className="auth-gate" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <p>正在校验登录状态…</p>
      </div>
    );
  }

  // 后端不可用 / 5xx：这是 error，不是未登录，禁止跳登录页吞掉真实故障。
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
          <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/shared-evidence" element={<SharedEvidence />} />
          <Route element={<RequireAuth />}>
            <Route element={<Layout />}>
              <Route path="/" element={<PreserveSearchRedirect to="/dashboard" />} />
              {/* AI-native 客户主链：总览 → 分析 → 验证 → 问题 → 发布 */}
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/analyze" element={<Analyze />} />
              <Route path="/verify" element={<Verify />} />
              <Route path="/findings" element={<Findings />} />
              <Route path="/findings/:id" element={<FindingDetail />} />
              <Route path="/release" element={<ReleaseGate />} />
              <Route path="/integration" element={<Integration />} />
              <Route path="/settings" element={<Settings />} />
              {/* 兼容与高级页面：保留原能力与 URL，主导航不再按内部流水线暴露。 */}
              <Route path="/requirements" element={<RequirementIntelligence />} />
              <Route path="/test-intelligence" element={<TestIntelligence />} />
              <Route path="/evidence" element={<EvidenceChain />} />
              <Route path="/materials" element={<Materials />} />
              <Route path="/jobs" element={<SystemJobs />} />
              <Route path="/campaigns" element={<EnterpriseCampaigns />} />
              <Route path="/coverage" element={<CoverageMatrix />} />
              {/* 向后兼容重定向：必须保留 project 等查询上下文 */}
              <Route path="/behavior-space" element={<PreserveSearchRedirect to="/coverage" />} />
              <Route path="/test-tasks" element={<PreserveSearchRedirect to="/campaigns" />} />
              <Route path="/clues" element={<PreserveSearchRedirect to="/settings" />} />
              <Route path="/products" element={<PreserveSearchRedirect to="/analyze" />} />
              {/* 未知旧链接保持 fail-safe：回到质量总览，不伪造页面状态。 */}
              <Route path="*" element={<PreserveSearchRedirect to="/dashboard" />} />
            </Route>
          </Route>
        </Routes>
        </BrowserRouter>
      </AuthProvider>
    </ToastProvider>
  );
}
