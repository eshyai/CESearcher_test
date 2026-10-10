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
  return res.data
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
}

export const getFocusTopics = async (): Promise<FocusTopic[]> => {
  const res = await request.get<FocusTopic[]>('/intelligence/topics', { loading: false })
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
  payload: { query_text?: string; weight?: number; is_active?: boolean },
): Promise<FocusTopic> => {
  const res = await request.patch<FocusTopic>(`/intelligence/topics/${topicId}`, payload)
  return res.data
}

export const deleteFocusTopic = async (topicId: string): Promise<{ success: boolean }> => {
  const res = await request.delete<{ success: boolean }>(`/intelligence/topics/${topicId}`)
  return res.data
}
