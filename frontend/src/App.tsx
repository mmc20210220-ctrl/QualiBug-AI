import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate, Outlet, useLocation } from 'react-router-dom';
import { Layout } from './components/Layout';
import { ToastProvider } from './components/Toast';
import { ScrollToTop } from './components/ScrollToTop';
import { AuthProvider } from './components/AuthProvider';
import { useAuth } from './components/useAuth';

const Dashboard = lazy(() => import('./pages/Dashboard').then((module) => ({ default: module.Dashboard })));
const RequirementIntelligence = lazy(() => import('./pages/RequirementIntelligence').then((module) => ({ default: module.RequirementIntelligence })));
const TestIntelligence = lazy(() => import('./pages/TestIntelligence').then((module) => ({ default: module.TestIntelligence })));
const Findings = lazy(() => import('./pages/Findings').then((module) => ({ default: module.Findings })));
const EvidenceChain = lazy(() => import('./pages/EvidenceChain').then((module) => ({ default: module.EvidenceChain })));
const ReleaseGate = lazy(() => import('./pages/ReleaseGate').then((module) => ({ default: module.ReleaseGate })));
const EnterpriseCampaigns = lazy(() => import('./pages/EnterpriseCampaigns').then((module) => ({ default: module.EnterpriseCampaigns })));
const CoverageMatrix = lazy(() => import('./pages/CoverageMatrix').then((module) => ({ default: module.CoverageMatrix })));
const SystemJobs = lazy(() => import('./pages/SystemJobs').then((module) => ({ default: module.SystemJobs })));
const Materials = lazy(() => import('./pages/Materials').then((module) => ({ default: module.Materials })));
const Settings = lazy(() => import('./pages/Settings').then((module) => ({ default: module.Settings })));
const Integration = lazy(() => import('./pages/Integration').then((module) => ({ default: module.Integration })));
const FindingDetail = lazy(() => import('./pages/FindingDetail').then((module) => ({ default: module.FindingDetail })));
const Login = lazy(() => import('./pages/Login').then((module) => ({ default: module.Login })));
const SharedEvidence = lazy(() => import('./pages/SharedEvidence').then((module) => ({ default: module.SharedEvidence })));

function RouteFallback() {
  return (
    <div className="auth-gate" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p>正在打开页面…</p>
    </div>
  );
}

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
          <Suspense fallback={<RouteFallback />}>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/shared-evidence" element={<SharedEvidence />} />
              <Route element={<RequireAuth />}>
                <Route element={<Layout />}>
                  <Route path="/" element={<PreserveSearchRedirect to="/requirements" />} />
                  {/* 客户主链：需求审查 / 测试智能 / 总览 / 问题 / 接入 */}
                  <Route path="/requirements" element={<RequirementIntelligence />} />
                  <Route path="/test-intelligence" element={<TestIntelligence />} />
                  <Route path="/dashboard" element={<Dashboard />} />
                  <Route path="/findings" element={<Findings />} />
                  <Route path="/findings/:id" element={<FindingDetail />} />
                  <Route path="/integration" element={<Integration />} />
                  {/* 技术/内部页面：退出客户一级导航，保留 URL 直访 */}
                  <Route path="/evidence" element={<EvidenceChain />} />
                  <Route path="/release" element={<ReleaseGate />} />
                  <Route path="/materials" element={<Materials />} />
                  <Route path="/jobs" element={<SystemJobs />} />
                  <Route path="/campaigns" element={<EnterpriseCampaigns />} />
                  <Route path="/coverage" element={<CoverageMatrix />} />
                  <Route path="/settings" element={<Settings />} />
                  {/* 向后兼容重定向：必须保留 project 等查询上下文 */}
                  <Route path="/behavior-space" element={<PreserveSearchRedirect to="/coverage" />} />
                  <Route path="/test-tasks" element={<PreserveSearchRedirect to="/campaigns" />} />
                  <Route path="/clues" element={<PreserveSearchRedirect to="/settings" />} />
                  <Route path="/products" element={<PreserveSearchRedirect to="/requirements" />} />
                  {/* 未知旧链接保持原 fail-safe：回到验证总览，不改变历史兼容语义。 */}
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
