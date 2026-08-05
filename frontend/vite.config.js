import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api/v1/proctor/ws': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
      '/api/v1/judge/ws': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/logos': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/uploads': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/resumes': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/cover_letters': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/interviews': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/mcq': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/proctor': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
