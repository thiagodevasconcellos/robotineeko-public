import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendTarget = 'http://127.0.0.1:8010'
const backendWsTarget = 'ws://127.0.0.1:8010'
const projectRoot = fileURLToPath(new URL('.', import.meta.url))

// https://vite.dev/config/
export default defineConfig({
    plugins: [react()],
    build: {
        rollupOptions: {
            input: {
                main: resolve(projectRoot, 'index.html'),
                mobile: resolve(projectRoot, 'mobile/index.html'),
            },
            output: {
                manualChunks(id) {
                    if (!id.includes('node_modules')) {
                        return null
                    }

                    if (id.includes('react') || id.includes('scheduler')) {
                        return 'vendor-react'
                    }

                    if (id.includes('lightweight-charts')) {
                        return 'vendor-chart'
                    }

                    if (id.includes('recharts')) {
                        return 'vendor-recharts'
                    }

                    return 'vendor'
                },
            },
        },
    },
    server: {
        watch: {
            ignored: [
                '**/.git/**',
                '**/node_modules/**',
                '**/dist/**',
                '**/backups/**',
                '**/backend/python/venv/**',
                '**/backend/python/**/__pycache__/**',
                '**/backend/python/data/**',
            ],
        },
        proxy: {
            '/bridge': {
                target: backendTarget,
                changeOrigin: true,
            },
            '/chart': {
                target: backendTarget,
                changeOrigin: true,
            },
            '/strategy': {
                target: backendTarget,
                changeOrigin: true,
            },
            '/auth': {
                target: backendTarget,
                changeOrigin: true,
            },
            '/workspace': {
                target: backendTarget,
                changeOrigin: true,
            },
            '/neural': {
                target: backendTarget,
                changeOrigin: true,
            },
            '/trade': {
                target: backendTarget,
                changeOrigin: true,
            },
            '/health': {
                target: backendTarget,
                changeOrigin: true,
            },
            '/system': {
                target: backendTarget,
                changeOrigin: true,
            },
            '/ws': {
                target: backendWsTarget,
                ws: true,
                changeOrigin: true,
            },
        },
    },
})
