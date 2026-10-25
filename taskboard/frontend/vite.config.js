import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
  build: {
    outDir: 'dist',
  },
  // Тесты компонентов: `npm test`, из общего прогона их зовёт
  // taskboard/tests/test_frontend_components.py. VITEST_FIXTURES=1 — вместо
  // них заведомо падающий тест: им проверяется, что падение доходит до прогона
  test: {
    environment: 'jsdom',
    include: process.env.VITEST_FIXTURES ? ['test-fixtures/*.test.js'] : ['src/**/*.test.{js,jsx}'],
  },
})
