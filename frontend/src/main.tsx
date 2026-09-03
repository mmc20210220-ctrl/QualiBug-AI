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
import './styles/finding-verification.css';
import './pages/TestDesign.css';
/* 遗留全局样式（渐进迁移中） */
import './index.css';
/* 客户主链响应式修正最后加载，覆盖遗留 class 与模块化 class 不一致 */
import './styles/customer-responsive.css';
import App from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);