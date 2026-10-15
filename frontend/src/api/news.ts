import { request } from './request'

// 资讯类型定义
export interface NewsItem {
  id: string
  title: string
  content: string
  source: string
  source_url: string
  category: string
  department: string
  publish_time: string
  collected_at: string
  keywords: string
  is_read: boolean
  created_at: string
  personalized_score?: number
  matched_topics?: string[]
  feedback?: {
    interested: boolean | null
    relevant: boolean | null
    note?: string | null
  } | null
  // ===== LLM 结构化抽取结果 =====
  is_duplicate?: boolean
  extracted_topics?: Array<{ type: string; name: string }>
  event_type?: string | null
  impact_direction?: 'positive' | 'negative' | 'neutral' | null
  key_numbers?: Array<{ name: string; value: string | number; unit?: string }>
  entities?: string[]
  summary?: string | null
  extraction_status?: 'pending' | 'completed' | 'failed' | string
  // ===== 碎片检索结果附加字段 =====
  score?: number
  match_reason?: string
}

export interface BiddingItem {
  id: string
  bid_id: string
  title: string
  notice_type: string
  province: string
  city: string
  content: string
  publish_time: string
  source: string
  collected_at: string
  is_read: boolean
  created_at: string
}

export interface NewsStats {
  total: number
  recent_24h: number
  by_category: Record<string, number>
}

export interface BiddingStats {
  total: number
  by_type: Record<string, number>
  by_province: Record<string, number>
}

export interface NewsListResponse {
  success: boolean
  data: NewsItem[]
  total: number
  stats: NewsStats
  /** 当前用户已屏蔽的条数（个人过滤，非全局删除），用于「已屏蔽」入口徽标 */
  hidden_count?: number
}

export interface BiddingListResponse {
  success: boolean
  data: BiddingItem[]
  total: number
  stats: BiddingStats
}

export interface CollectionResponse {
  success: boolean
  message: string
  news_collected: number
  bidding_collected: number
  duplicates_filtered?: number
  low_quality_filtered?: number
  extraction?: {
    processed?: number
    succeeded?: number
    failed?: number
    skipped?: string
  } | null
  errors: string[]
}

// 获取行业资讯列表
export const getNewsList = async (params?: {
  category?: string
  industry_id?: string
  event_type?: string
  impact_direction?: string
  limit?: number
  offset?: number
}): Promise<NewsListResponse> => {
  // loading: false 禁用全局 loading，使用页面的骨架屏
  const res = await request.get<NewsListResponse>('/news/list', { params, loading: false })
  return res.data
}

// 碎片检索：输入公司/产品/工艺/政策片段等，返回相关报道（向量语义 ∪ 关键词 + rerank）
export interface NewsSearchResponse {
  success: boolean
  query: string
  mode: string
  vector_indexed: boolean
  total: number
  data: NewsItem[]
  online_total?: number
  online_kept?: number
  persisted?: number
  title_deduped?: number
  foreign_filtered?: number
}

export const searchNews = async (params: {
  query: string
  industry_id?: string
  event_type?: string
  impact_direction?: string
  limit?: number
  include_online?: boolean
  exclude_foreign?: boolean
}): Promise<NewsSearchResponse> => {
  // loading: false 用页面自己的检索态；cancelRepeat: false 避免快速连续检索被取消
  const res = await request.post<NewsSearchResponse>('/news/search', params, {
    loading: false,
    cancelRepeat: false,
    // 全网实时检索 + 重排，耗时比普通接口长，放宽超时
    timeout: 60000,
  })
  return res.data
}

// ===========================================================================
// 个人屏蔽（只影响自己，不是删除）
// 资讯是**按行业共享的语料**，删一条所有人都看不到，还会连带删掉所有人的反馈
// （反馈是偏好学习的输入）。所以这里只做个人过滤：自己看不到，随时可恢复。
// ===========================================================================

export interface HideActionResponse {
  success: boolean
  message: string
  /** created / exists / removed / absent */
  status: string
  hidden_count: number
}

export interface HiddenNewsListResponse {
  success: boolean
  data: NewsItem[]
  total: number
}

// 屏蔽一条资讯（幂等；不写反馈，不影响偏好学习样本）
export const hideNews = async (newsId: string, reason?: string): Promise<HideActionResponse> => {
  const res = await request.post<HideActionResponse>(
    `/news/${newsId}/hide`,
    reason ? { reason } : {},
    { loading: false },
  )
  return res.data
}

// 取消屏蔽，恢复显示（幂等）
export const unhideNews = async (newsId: string): Promise<HideActionResponse> => {
  const res = await request.delete<HideActionResponse>(`/news/${newsId}/hide`, { loading: false })
  return res.data
}

// 已屏蔽列表（按屏蔽时间倒序，用于查看与恢复）
export const getHiddenNews = async (params?: {
  limit?: number
  offset?: number
}): Promise<HiddenNewsListResponse> => {
  const res = await request.get<HiddenNewsListResponse>('/news/hidden', { params, loading: false })
  return res.data
}

// 获取招投标列表
export const getBiddingList = async (params?: {
  notice_type?: string
  province?: string
  industry_id?: string
  limit?: number
  offset?: number
}): Promise<BiddingListResponse> => {
  // loading: false 禁用全局 loading，使用页面的骨架屏
  const res = await request.get<BiddingListResponse>('/news/bidding/list', { params, loading: false })
  return res.data
}

// 获取所有统计
export const getStats = async () => {
  console.log('[API] getStats 请求')
  const res = await request.get<{
    success: boolean
    news: NewsStats
    bidding: BiddingStats
  }>('/news/stats')
  console.log('[API] getStats 响应:', res.data)
  return res.data
}

// 手动触发采集
export const triggerCollection = async (params?: {
  max_news?: number
  max_bidding?: number
  industry_id?: string
}): Promise<CollectionResponse> => {
  // loading: false 禁用全局 loading，按钮已有自己的 loading 状态
  // cancelRepeat: false 禁用取消重复请求（采集是长时间操作）
  // timeout: 300000 设置5分钟超时（采集+LLM抽取全流程实测约 170s，留足余量）
  const res = await request.post<CollectionResponse>('/news/collect', null, {
    params,
    loading: false,
    cancelRepeat: false,
    timeout: 300000,
  })
  return res.data
}

// 获取行业列表
export const getIndustries = async () => {
  console.log('[API] getIndustries 请求')
  const res = await request.get<{
    success: boolean
    industries: Array<{
      id: string
      name: string
      description: string
    }>
  }>('/news/industries')
  console.log('[API] getIndustries 响应:', res.data)
  return res.data
}

// 获取单个行业配置
export const getIndustry = async (industryId: string) => {
  console.log('[API] getIndustry 请求:', industryId)
  const res = await request.get<{
    success: boolean
    industry: {
      id: string
      name: string
      description: string
      news_keywords: string[]
      bidding_keywords: string[]
    }
  }>(`/news/industries/${industryId}`)
  console.log('[API] getIndustry 响应:', res.data)
  return res.data
}

// 检查数据状态
export const checkDataStatus = async () => {
  const res = await request.get<{
    success: boolean
    has_data: boolean
    news_count: number
    bidding_count: number
    news_recent_24h: number
  }>('/news/check')
  return res.data
}

// 获取定时任务状态
export const getSchedulerStatus = async () => {
  const res = await request.get<{
    success: boolean
    jobs: Array<{
      id: string
      name: string
      next_run_time: string
      trigger: string
    }>
  }>('/news/scheduler/status')
  return res.data
}

export const submitNewsFeedback = async (
  newsId: string,
  payload: { interested?: boolean; relevant?: boolean; note?: string },
) => {
  const res = await request.post(`/intelligence/news/${newsId}/feedback`, payload, {
    loading: false,
  })
  return res.data as {
    success: boolean
    /** 权重被更新的已有主题 */
    updated_topics: FocusTopic[]
    /** L1.5：本次由反馈自动派生的候选主题 */
    created_topics: FocusTopic[]
    /** L1.5：被负反馈自动停用的候选主题 */
    retired_topics: FocusTopic[]
  }
}

export interface FocusTopic {
  id: string
  topic_type: 'industry' | 'customer' | 'process' | 'technology' | 'product'
  name: string
  query_text: string
  weight: number
  positive_count: number
  negative_count: number
  is_active: boolean
  /** manual=用户手动创建；auto=反馈自动派生（L1.5） */
  source: 'manual' | 'auto'
  /** 自动主题的触发资讯 id（可解释性） */
  origin_news_id?: string | null
  /** 自动主题被反馈命中的次数 */
  auto_hits?: number
}

export const getFocusTopics = async (params?: {
  source?: 'manual' | 'auto'
  include_inactive?: boolean
}): Promise<FocusTopic[]> => {
  const res = await request.get<FocusTopic[]>('/intelligence/topics', {
    loading: false,
    params,
  })
  return res.data
}

export const createFocusTopic = async (payload: {
  topic_type: FocusTopic['topic_type']
  name: string
  query_text: string
  weight?: number
}): Promise<FocusTopic> => {
  const res = await request.post<FocusTopic>('/intelligence/topics', payload)
  return res.data
}

export const updateFocusTopic = async (
  topicId: string,
  payload: {
    query_text?: string
    weight?: number
    is_active?: boolean
    /** 把自动派生的候选主题「确认为关注」（auto -> manual） */
    source?: FocusTopic['source']
  },
): Promise<FocusTopic> => {
  const res = await request.patch<FocusTopic>(`/intelligence/topics/${topicId}`, payload)
  return res.data
}

export const deleteFocusTopic = async (topicId: string): Promise<{ success: boolean }> => {
  const res = await request.delete<{ success: boolean }>(`/intelligence/topics/${topicId}`)
  return res.data
}

// ===========================================================================
// L2a 归纳引擎：建议箱 + 归纳历史 + 回滚
// 模型永远不直接改关注主题，只产出「建议 + 理由 + 证据」，由用户逐条确认。
// ===========================================================================

export type SuggestionKind = 'add' | 'adjust' | 'merge' | 'drop' | 'insight'

export interface PreferenceSuggestion {
  id: string
  kind: SuggestionKind
  name: string
  target_id?: string | null
  /** add: topic_type/query_text/weight/alias_count；adjust: delta/from_weight；merge: targets/into/topic_ids */
  payload: Record<string, any>
  reason?: string | null
  /** 0 ~ 1，越高越排前 */
  confidence: number
  /** 支撑这条建议的反馈 id 列表 */
  evidence: string[]
  status: 'pending' | 'accepted' | 'rejected'
  revision_id?: string | null
  resolved_at?: string | null
  created_at?: string | null
}

export interface InductionStatus {
  /** 是否已达到归纳条件（反馈条数达阈值，或距上次归纳超过 N 天） */
  due: boolean
  reason: 'batch' | 'schedule' | 'insufficient'
  /** 距上次归纳后新增的反馈条数 */
  pending_feedback: number
  threshold: number
  max_days: number
  days_since_last?: number | null
  last_inducted_at?: string | null
  /** 待确认建议数（决定按钮徽标） */
  pending_suggestions: number
}

export interface FocusTopicRevision {
  id: string
  trigger: 'manual' | 'batch' | 'schedule'
  sample_count: number
  topic_count: number
  status: 'applied' | 'rolled_back'
  current: boolean
  summary?: string | null
  error?: string | null
  diff: Array<{ kind: SuggestionKind; name: string; reason?: string; confidence?: number }>
  created_at?: string | null
}

export const getInductionStatus = async (): Promise<InductionStatus> => {
  const res = await request.get<InductionStatus>('/intelligence/induction/status', { loading: false })
  return res.data
}

/** 运行一次归纳（同步调用大模型，可能数秒到数十秒）。返回 ok=false 表示没样本 / 模型不可用 */
export const runInduction = async (
  force = true,
): Promise<{
  ok: boolean
  reason?: string
  message?: string
  created?: number
  insights?: string[]
  revision?: FocusTopicRevision
  suggestions?: PreferenceSuggestion[]
}> => {
  const res = await request.post(
    '/intelligence/induction',
    { force },
    // 归纳要同步调大模型，默认超时不够，单独放宽
    { loading: false, timeout: 120000 },
  )
  return res.data
}

export const getSuggestions = async (
  params?: { status?: 'pending' | 'accepted' | 'rejected' | 'all'; kind?: SuggestionKind },
): Promise<PreferenceSuggestion[]> => {
  const res = await request.get<PreferenceSuggestion[]>('/intelligence/suggestions', {
    loading: false,
    params,
  })
  return res.data
}

export const acceptSuggestion = async (
  suggestionId: string,
): Promise<{
  success: boolean
  suggestion: PreferenceSuggestion
  touched_topics: FocusTopic[]
  detail: string
}> => {
  const res = await request.post(`/intelligence/suggestions/${suggestionId}/accept`)
  return res.data
}

export const rejectSuggestion = async (
  suggestionId: string,
): Promise<{ success: boolean; suggestion: PreferenceSuggestion; detail: string }> => {
  const res = await request.post(`/intelligence/suggestions/${suggestionId}/reject`)
  return res.data
}

export const getRevisions = async (limit = 20): Promise<FocusTopicRevision[]> => {
  const res = await request.get<FocusTopicRevision[]>('/intelligence/revisions', {
    loading: false,
    params: { limit },
  })
  return res.data
}

export const rollbackRevision = async (
  revisionId: string,
): Promise<{ success: boolean; revision: FocusTopicRevision; restored: string[]; removed: string[] }> => {
  const res = await request.post(`/intelligence/revisions/${revisionId}/rollback`)
  return res.data
}
