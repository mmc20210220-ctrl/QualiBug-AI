import { useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

export function buildProjectPath(pathname: string, projectId?: string, currentSearch = '') {
  const normalizedPath = pathname.startsWith('/') ? pathname : `/${pathname}`;
  const nextId = String(projectId || '').trim();
  if (!nextId) return normalizedPath;

  const params = new URLSearchParams(currentSearch);
  params.set('project', nextId);
  const search = params.toString();

  return `${normalizedPath}${search ? `?${search}` : ''}`;
}

export function useProjectNavigation() {
  const location = useLocation();
  const navigate = useNavigate();

  const switchProject = useCallback((projectId: string) => {
    const target = buildProjectPath(location.pathname, projectId, location.search);
    navigate(target);
  }, [location.pathname, location.search, navigate]);

  const navigateToProjectPath = useCallback((pathname: string, projectId?: string, currentSearch = '') => {
    const target = buildProjectPath(pathname, projectId, currentSearch);
    navigate(target);
  }, [navigate]);

  return {
    buildProjectPath,
    switchProject,
    navigateToProjectPath,
  };
}
