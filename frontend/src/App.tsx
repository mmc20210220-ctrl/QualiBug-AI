import { BrowserRouter, Routes, Route, Navigate, Outlet, useLocation } from 'react-router-dom';
import { Layout } from './components/Layout';
import { ToastProvider } from './components/Toast';
import { ScrollToTop } from './components/ScrollToTop';
import { isAuthenticated } from './api/client';
import { Dashboard } from './pages/Dashboard';
import { Findings } from './pages/Findings';
import { EvidenceChain } from './pages/EvidenceChain';
import { ReleaseGate } from './pages/ReleaseGate';
import { EnterpriseCampaigns } from './pages/EnterpriseCampaigns';
import { CoverageMatrix } from './pages/CoverageMatrix';
import { SystemJobs } from './pages/SystemJobs';
import { Materials } from './pages/Materials';
import { Settings } from './pages/Settings';
import { Login } from './pages/Login';
import { SharedEvidence } from './pages/SharedEvidence';

function RequireAuth() {
  const location = useLocation();
  if (!isAuthenticated()) {
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
      <BrowserRouter>
        <ScrollToTop />
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/shared-evidence" element={<SharedEvidence />} />
          <Route element={<RequireAuth />}>
            <Route element={<Layout />}>
              <Route path="/" element={<PreserveSearchRedirect to="/dashboard" />} />
              {/* 成果面 */}
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/findings" element={<Findings />} />
              <Route path="/evidence" element={<EvidenceChain />} />
              <Route path="/release" element={<ReleaseGate />} />
              {/* 企业认知 */}
              <Route path="/materials" element={<Materials />} />
              <Route path="/jobs" element={<SystemJobs />} />
              {/* 执行面 */}
              <Route path="/campaigns" element={<EnterpriseCampaigns />} />
              <Route path="/coverage" element={<CoverageMatrix />} />
              {/* 配置 */}
              <Route path="/settings" element={<Settings />} />
              {/* 向后兼容重定向：必须保留 project 等查询上下文 */}
              <Route path="/behavior-space" element={<PreserveSearchRedirect to="/coverage" />} />
              <Route path="/test-tasks" element={<PreserveSearchRedirect to="/campaigns" />} />
              <Route path="/clues" element={<PreserveSearchRedirect to="/settings" />} />
              <Route path="/products" element={<PreserveSearchRedirect to="/dashboard" />} />
              {/* 未知旧链接 fail-safe 回到当前客户总览，而不是渲染空白页 */}
              <Route path="*" element={<PreserveSearchRedirect to="/dashboard" />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </ToastProvider>
  );
}
