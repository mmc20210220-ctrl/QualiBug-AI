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

function RequireAuth() {
  const location = useLocation();
  if (!isAuthenticated()) {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }
  return <Outlet />;
}

export default function App() {
  return (
    <ToastProvider>
      <BrowserRouter>
        <ScrollToTop />
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<RequireAuth />}>
            <Route element={<Layout />}>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
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
              {/* 向后兼容重定向 */}
              <Route path="/behavior-space" element={<Navigate to="/coverage" replace />} />
              <Route path="/test-tasks" element={<Navigate to="/campaigns" replace />} />
              <Route path="/clues" element={<Navigate to="/settings" replace />} />
              <Route path="/products" element={<Navigate to="/dashboard" replace />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </ToastProvider>
  );
}
