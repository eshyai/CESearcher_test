/**
 * 未授权插件：401 时清除本地登录态并跳转登录页
 *
 * 背景：JWT 有效期 24 小时，前端路由守卫只检查本地是否存在 token、
 * 不校验其有效性，导致携带过期 token 访问页面时反复弹出 401。
 * 此处统一兜底：清除登录态 + 跳转登录页，避免死循环弹窗。
 */
import { IRequestPlugin } from './plugin'

const AUTH_STORAGE_KEY = 'auth'

// 登录/注册接口自身的 401 属于业务错误（账号或密码错误），不应触发跳转
const AUTH_ENDPOINTS = ['/auth/token', '/auth/login', '/auth/register']

// 防止并发请求同时触发多次跳转
let redirecting = false

function redirectToLogin() {
  if (redirecting) return
  redirecting = true

  try {
    localStorage.removeItem(AUTH_STORAGE_KEY)
  } catch {
    // ignore
  }

  const base = (import.meta.env.BASE_URL || '/').replace(/\/$/, '')
  const loginPath = `${base}/login`

  if (window.location.pathname === loginPath) {
    redirecting = false
    return
  }

  window.location.replace(loginPath)
}

export const unauthorizedPlugin: IRequestPlugin = {
  postinstall(instance) {
    instance.interceptors.response.use(
      (response) => response,
      (error) => {
        const status = error?.response?.status
        const url: string = error?.config?.url || ''

        if (status === 401 && !AUTH_ENDPOINTS.some((endpoint) => url.includes(endpoint))) {
          window.$app?.message?.warning?.('登录状态已过期，请重新登录')
          redirectToLogin()
        }

        return Promise.reject(error)
      },
    )
  },
}
