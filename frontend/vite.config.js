import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
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
  }
})
