import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(() => {
  return {
    plugins: [react()],
    server: {
      port: 5174,
      strictPort: true,
      proxy: {
        // Single backend: the private pilot service on 8088 serves both the API
        // and the prebuilt SPA. The legacy /enterprise-api -> :8000 route was dead
        // (no listener in any deploy) and has been removed to eliminate the
        // double-backend inconsistency. Frontend dev = 5174, backend = 8088.
        '/api': {
          target: 'http://127.0.0.1:8088',
          changeOrigin: true,
        },
      },
    },
  };
});
