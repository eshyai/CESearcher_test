/**
 * 全局行业状态管理
 *
 * 当前产品聚焦化工垂直场景，前端只暴露 chemical 一个行业。
 * 智慧交通 / 金融科技 / 医疗健康 / 能源电力 已从前端移除（后端配置保留），
 * 后续接入新行业时，在本数组追加一项即可——首页卡片、导航选择器、
 * 各页面的 industry_id 传参都会自动跟着走。
 */
import { proxy, subscribe } from 'valtio'

// 行业配置类型
export interface IndustryConfig {
  id: string
  name: string
  description: string
  // 资讯搜索关键词
  newsKeywords: string[]
  // 招投标搜索关键词
  biddingKeywords: string[]
  // 研究相关关键词
  researchKeywords: string[]
}

// 预定义的行业配置
export const INDUSTRY_CONFIGS: IndustryConfig[] = [
  {
    id: 'chemical',
    name: '化工',
    description: '石油化工、精细化工、化工新材料、危化品监管与双碳政策等领域',
    newsKeywords: [
      '化工 政策',
      '危化品 监管',
      '化工行业 产能',
      '化工品 价格行情',
      '化工 项目 投产',
      '石化 装置 检修',
      '化工 安全事故',
      '双碳 化工',
    ],
    biddingKeywords: ['化工', '危化品', '石化工程', '化工园区', '化工设备', '环保治理'],
    researchKeywords: ['化工', '石化', '危化品', '化工品价格'],
  },
]

// 行业状态
export interface IndustryState {
  currentIndustryId: string
  industries: IndustryConfig[]
}

// 默认行业（化工资讯智能体为主场景）
const DEFAULT_INDUSTRY_ID = 'chemical'

// 从 localStorage 读取（校验有效性，避免残留的旧行业 id 导致空白）
const getStoredIndustryId = (): string => {
  if (typeof window !== 'undefined') {
    const stored = localStorage.getItem('selected_industry_id')
    console.log('[industry store] 从 localStorage 读取行业:', stored)
    if (stored && INDUSTRY_CONFIGS.some((i) => i.id === stored)) {
      return stored
    }
  }
  return DEFAULT_INDUSTRY_ID
}

// 创建状态
export const industryState = proxy<IndustryState>({
  currentIndustryId: getStoredIndustryId(),
  industries: INDUSTRY_CONFIGS,
})

// 订阅变化，保存到 localStorage
subscribe(industryState, () => {
  if (typeof window !== 'undefined') {
    console.log('[industry store] 保存行业到 localStorage:', industryState.currentIndustryId)
    localStorage.setItem('selected_industry_id', industryState.currentIndustryId)
  }
})

// 获取当前行业配置
export const getCurrentIndustry = (): IndustryConfig => {
  const industry = industryState.industries.find(
    (i) => i.id === industryState.currentIndustryId
  )
  console.log('[industry store] 获取当前行业:', industry?.name)
  return industry || INDUSTRY_CONFIGS[0]
}

// 切换行业
export const setCurrentIndustry = (industryId: string) => {
  console.log('[industry store] 切换行业:', industryId)
  industryState.currentIndustryId = industryId
}

// 获取行业列表（用于选择器）
export const getIndustryOptions = () => {
  return industryState.industries.map((i) => ({
    value: i.id,
    label: i.name,
    description: i.description,
  }))
}
