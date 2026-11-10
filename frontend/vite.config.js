import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // 浏览器始终请求 /api；开发时由 Vite 转给后端。
    proxy: {
      '/api': { target: process.env.REPORT_AGENT_BACKEND_URL || 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    // 这个目录只存前端构建产物，每次 build 会重新生成。
    outDir: fileURLToPath(new URL('../backend/static', import.meta.url)),
    emptyOutDir: true,
  },
});
