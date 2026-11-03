import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In development the gateway runs on :4000; proxy REST and the Socket.io stream to it.
const gateway = process.env.GATEWAY_URL || 'http://localhost:4000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: gateway, changeOrigin: true },
      '/socket.io': { target: gateway, ws: true, changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false },
  test: { environment: 'node' },
});
