import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/status': 'http://localhost:8080',
      '/trades': 'http://localhost:8080',
      '/signals': 'http://localhost:8080',
      '/positions': 'http://localhost:8080',
      '/logs': 'http://localhost:8080',
    },
  },
})
