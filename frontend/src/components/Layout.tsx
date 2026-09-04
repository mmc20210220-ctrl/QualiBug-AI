import { useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { MaterialsOnboardingHandoff } from './materials/MaterialsOnboardingHandoff';
import { RunCustomerResultSummary } from './run/RunCustomerResultSummary';
import { RunLifecycleBanner } from './run/RunLifecycleBanner';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';

const RUN_CONTEXT_PATHS = new Set(['/campaigns', '/coverage', '/jobs']);

export function Layout() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const location = useLocation();
  const isFocusedWorkspace = location.pathname === '/analyze'
    || location.pathname === '/verify'
    || location.pathname === '/requirements'
    || location.pathname === '/test-intelligence';
  const showRunContext = !isFocusedWorkspace && RUN_CONTEXT_PATHS.has(location.pathname);
  const showOnboardingHandoff = location.pathname === '/integration';

  useEffect(() => {
    const closeOnDesktop = () => {
      if (window.innerWidth > 1024) setMobileNavOpen(false);
    };
    closeOnDesktop();
    window.addEventListener('resize', closeOnDesktop);
    return () => window.removeEventListener('resize', closeOnDesktop);
  }, []);

  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname, location.search]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileNavOpen(false);
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [mobileNavOpen]);

  return (
    <div className="shell">
      <Sidebar mobileOpen={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
      <main className="main">
        <Topbar navOpen={mobileNavOpen} onToggleNav={() => setMobileNavOpen((open) => !open)} />
        <div className="content">
          {showRunContext && <RunCustomerResultSummary />}
          {showRunContext && <RunLifecycleBanner />}
          {showOnboardingHandoff && <MaterialsOnboardingHandoff />}
          <Outlet />
        </div>
      </main>
    </div>
  );
}
