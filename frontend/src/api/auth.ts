import { request } from './request'

export interface LoginParams {
  username: string
  password: string
}

export interface RegisterParams {
  username: string
  email: string
  password: string
  /** 邀请码：站点关闭自助注册时后端会要求（由 ALLOW_REGISTER / REGISTER_INVITE_CODE 决定） */
  invite_code?: string
}

export interface UserInfo {
  id: string
  username: string
  email: string
  is_active: boolean
  created_at: string
}

export interface AuthResponse {
  access_token: string
  token_type: string
  user: UserInfo
}

/** 站点注册准入策略（登录页据此决定是否显示「邀请码」输入框） */
export interface RegistrationPolicy {
  /** 是否开放自助注册（开放时无需邀请码） */
  allow_register: boolean
  /** 是否必须提供邀请码 */
  require_invite: boolean
  /** 是否至少有一种自助注册途径（开放注册 或 凭邀请码） */
  registration_open: boolean
}

/**
 * 用户登录
 */
export function login(params: LoginParams) {
  return request.post<AuthResponse>('/auth/login', params)
}

/**
 * 查询注册准入策略（公开接口）
 */
export function getRegistrationPolicy() {
  return request.get<RegistrationPolicy>('/auth/registration-policy')
}

/**
 * 用户注册
 */
export function register(params: RegisterParams) {
  return request.post<AuthResponse>('/auth/register', params)
}

/**
 * 获取当前用户信息
 */
export function getCurrentUser() {
  return request.get<UserInfo>('/auth/me')
}

/**
 * 用户登出
 */
export function logout() {
  return request.post('/auth/logout')
}

/**
 * 修改密码
 */
export function changePassword(params: { old_password: string; new_password: string }) {
  return request.post('/auth/change-password', params)
}
