import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the gateway runs on :4000; proxy REST and the Socket.io stream to it.
// changeOrigin stays false: the gateway accepts a browser request only when its Origin matches the
// Host header, so the proxy must pass the dev server's own Host (localhost:5173) through.
const gateway = process.env.GATEWAY_URL || 'http://localhost:4000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: gateway, changeOrigin: false },
      '/socket.io': { target: gateway, ws: true, changeOrigin: false },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
  test: { environment: 'node' },
});
