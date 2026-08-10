import { useEffect, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { RunLifecycleBanner } from './run/RunLifecycleBanner';
import { Sidebar } from './Sidebar';
import { Topbar } from './Topbar';

export function Layout() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const location = useLocation();

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
          <RunLifecycleBanner />
          <Outlet />
        </div>
      </main>
    </div>
  );
}
