import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 7888,
    proxy: {
      '/api': 'http://localhost:5173',
      '/docs': 'http://localhost:5173',
      '/redoc': 'http://localhost:5173',
      '/openapi.json': 'http://localhost:5173',
    },
    allowedHosts: ['internal.kixlab.org']
  },
  build: {
    outDir: 'dist'
  }
})
