import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'
import type { ProxyOptions } from 'vite'
import { viteMockServe } from 'vite-plugin-mock'

// 区分「前端路由」与「后端 API」请求。
//
// 背景：代理键是前缀匹配（/chat 会命中 /chat、/chat/session），而前端页面路由
// 恰好也叫 /chat、/news、/database。在地址栏直接访问或刷新这些页面时，浏览器发
// 的是 GET /chat，会被代理转发到后端；后端没有 GET /chat 路由，返回
// {"detail":"Not Found"}，于是白屏。
//
// 判定依据：浏览器地址栏访问 / 刷新页面（HTML 导航）时 Accept 含 text/html；
// axios/fetch 的接口请求 Accept 为 application/json。
// 因此 HTML 导航一律跳过代理、交回 Vite 走 SPA fallback（返回 index.html）；
// 返回字符串 = 跳过代理，返回 undefined = 照常代理。
const bypassHtmlNavigation: ProxyOptions['bypass'] = (req) => {
  if (req.headers.accept?.includes('text/html')) {
    return req.url
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd()) as {
    VITE_API_BASE: string
    VITE_API_PROXY: string
  }

  const target = env.VITE_API_PROXY

  return {
    server: {
      port: 5183,
      host: '0.0.0.0',
      proxy: {
        '/auth': { target, bypass: bypassHtmlNavigation },
        '/sessions': { target, bypass: bypassHtmlNavigation },
        '/attachments': { target, bypass: bypassHtmlNavigation },
        '/chat': { target, bypass: bypassHtmlNavigation },
        '/database': { target, bypass: bypassHtmlNavigation },
        '/documents': { target, bypass: bypassHtmlNavigation },
        '/intelligence': { target, bypass: bypassHtmlNavigation },
        '/knowledge-bases': { target, bypass: bypassHtmlNavigation },
        '/memories': { target, bypass: bypassHtmlNavigation },
        '/news': { target, bypass: bypassHtmlNavigation },
        '/research': { target, bypass: bypassHtmlNavigation },
        '/search': { target, bypass: bypassHtmlNavigation },
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
