import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
/* 模块化 CSS */
import './styles/variables.css';
import './styles/base.css';
import './styles/animations.css';
import './styles/layout.css';
import './styles/components.css';
import './styles/dashboard.css';
import './styles/findings.css';
import './styles/evidence.css';
/* 遗留全局样式（渐进迁移中） */
import './index.css';
import App from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
