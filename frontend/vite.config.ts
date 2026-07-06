import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const enterpriseApiOrigin = env.QUALIBUG_ENTERPRISE_API_ORIGIN || 'http://127.0.0.1:8000';
  return {
    plugins: [react()],
    server: {
      port: 5174,
      strictPort: true,
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8088',
          changeOrigin: true,
        },
        '/enterprise-api': {
          target: enterpriseApiOrigin,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/enterprise-api/, ''),
        },
      },
    },
  };
});
