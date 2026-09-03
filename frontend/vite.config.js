import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    host: true,
    allowedHosts: ['.trycloudflare.com', 'localhost', '127.0.0.1'],
    proxy: {
      '/api': {
        target: 'http://localhost:8002',
        changeOrigin: true
      },
      // Proxy WebSocket connections for channels (Django Channels)
      '/ws': {
        target: 'ws://localhost:8002',
        ws: true,
        changeOrigin: true
      }
    }
  },
  // Component tests. `environment: 'jsdom'` because everything under test
  // renders; `setupFiles` installs the jest-dom matchers and resets the
  // localStorage the API client uses between tests, so one test's stored
  // token cannot make the next one pass.
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    include: ['src/**/*.test.{js,jsx}'],
  }
})
