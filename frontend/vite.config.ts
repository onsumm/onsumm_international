/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Pin the framework into its own chunk. It changes far less often than
        // the app does, so a normal deploy invalidates the app chunks and leaves
        // this one in the browser cache instead of re-downloading React on every
        // release. react-pdf is separated for the same reason and because only
        // three routes ever load it.
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          pdf: ['react-pdf'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8001',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.ts'],
    globals: true,
    include: ['src/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/setupTests.ts'],
    },
  },
})
