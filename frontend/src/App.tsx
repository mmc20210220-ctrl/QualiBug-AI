import { BrowserRouter, Routes, Route, Navigate, Outlet, useLocation } from 'react-router-dom';
import { Layout } from './components/Layout';
import { ToastProvider } from './components/Toast';
import { ScrollToTop } from './components/ScrollToTop';
import { isAuthenticated } from './api/client';
import { Dashboard } from './pages/Dashboard';
import { Findings } from './pages/Findings';
import { InternalClues } from './pages/InternalClues';
import { EvidenceChain } from './pages/EvidenceChain';
import { BehaviorSpace } from './pages/BehaviorSpace';
import { EnterpriseMaterials } from './pages/EnterpriseMaterials';
import { EnterpriseCampaigns } from './pages/EnterpriseCampaigns';
import { ReleaseGate } from './pages/ReleaseGate';
import { Settings } from './pages/Settings';
import { Products } from './pages/Products';
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
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/findings" element={<Findings />} />
              <Route path="/clues" element={<InternalClues />} />
              <Route path="/evidence" element={<EvidenceChain />} />
              <Route path="/behavior-space" element={<BehaviorSpace />} />
              <Route path="/materials" element={<EnterpriseMaterials />} />
              <Route path="/campaigns" element={<EnterpriseCampaigns />} />
              <Route path="/release" element={<ReleaseGate />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/products" element={<Products />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </ToastProvider>
  );
}
