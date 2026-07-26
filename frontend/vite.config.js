import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Dev proxy: forward /api to the local backend so the browser can call a
    // same-origin URL (matches the nginx setup in production). In dev the
    // VITE_API_KEY still comes from a local .env so requests are authenticated.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
