import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/ui/',
  server: {
    proxy: {
      '/api': 'http://localhost:8090',
      '/healthz': 'http://localhost:8090',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
