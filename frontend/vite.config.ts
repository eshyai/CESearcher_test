import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'
import { viteMockServe } from 'vite-plugin-mock'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd()) as {
    VITE_API_BASE: string
    VITE_API_PROXY: string
  }

  return {
    server: {
      port: 5183,
      host: '0.0.0.0',
      proxy: {
  '/auth': env.VITE_API_PROXY,
  '/sessions': env.VITE_API_PROXY,
  '/attachments': env.VITE_API_PROXY,
  '/chat': env.VITE_API_PROXY,
  '/database': env.VITE_API_PROXY,
  '/documents': env.VITE_API_PROXY,
  '/intelligence': env.VITE_API_PROXY,
  '/knowledge-bases': env.VITE_API_PROXY,
  '/memories': env.VITE_API_PROXY,
  '/news': env.VITE_API_PROXY,
  '/research': env.VITE_API_PROXY,
  '/search': env.VITE_API_PROXY,
            },
    },
    resolve: {
      alias: [
        {
          find: /^@\//,
          replacement: '/src/',
        },
      ],
    },

    plugins: [
      react(),
      viteMockServe({
        enable: false,
      }),
    ],
  }
})
