import { useEffect } from 'react'
import { authActions, authState } from '@/store/auth'
import { Navigate, useLocation } from 'react-router-dom'
import { useSnapshot } from 'valtio'

interface AuthGuardProps {
  children: React.ReactNode
}

/**
 * 判断 JWT 是否已过期（仅解析 payload.exp，不做签名校验）
 * 解析失败时返回 false，交由后端 401 兜底
 */
function isTokenExpired(token: string | null): boolean {
  if (!token) return true
  try {
    const payload = JSON.parse(atob(token.split('.')[1] || ''))
    if (!payload?.exp) return false
    return payload.exp * 1000 <= Date.now()
  } catch {
    return false
  }
}

/**
 * 认证守卫组件
 *
 * 检查用户是否已登录且 token 未过期，否则重定向到登录页面
 * 登录后会自动跳转回原来的页面
 */
export function AuthGuard({ children }: AuthGuardProps) {
  const { isLoggedIn, token } = useSnapshot(authState)
  const location = useLocation()

  // 本地存在登录态但 token 已过期：清理掉，避免后续请求持续 401
  const expired = isLoggedIn && isTokenExpired(token)

  useEffect(() => {
    if (expired) {
      authActions.logout()
    }
  }, [expired])

  if (!isLoggedIn || expired) {
    // 保存当前路径，登录后可以跳转回来
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return <>{children}</>
}
