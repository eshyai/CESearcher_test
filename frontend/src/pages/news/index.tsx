import { useEffect, useRef, useState, useCallback } from 'react'
import {
  Card,
  Button,
  Tag,
  message,
  Empty,
  Skeleton,
  Pagination,
  Statistic,
  Row,
  Col,
  Input,
  Select,
  Switch,
  Drawer,
  Popover,
} from 'antd'
import {
  FileTextOutlined,
  ClockCircleOutlined,
  BankOutlined,
  LinkOutlined,
  LikeOutlined,
  DislikeOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  EyeInvisibleOutlined,
  UndoOutlined,
  EditOutlined,
  BookOutlined,
} from '@ant-design/icons'
import { useSnapshot } from 'valtio'
import { authState } from '@/store/auth'
import { industryState, getCurrentIndustry } from '@/store/industry'
import { useNavigate, useSearchParams } from 'react-router-dom'
import * as api from '@/api'
import type { NewsItem, NewsStats } from '@/api/news'
import styles from './index.module.scss'

// 资讯分类
const NEWS_CATEGORIES = [
  { label: '全部', value: '' },
  { label: '政策', value: '政策' },
  { label: '研报', value: '研报' },
  { label: '新闻', value: '新闻' },
]

// LLM 抽取的事件类型（与后端 event_type 枚举对齐）
const EVENT_TYPE_OPTIONS = [
  { label: '投产扩产', value: '投产扩产' },
  { label: '停产检修', value: '停产检修' },
  { label: '价格变动', value: '价格变动' },
  { label: '政策发布', value: '政策发布' },
  { label: '安全事故', value: '安全事故' },
  { label: '环保处罚', value: '环保处罚' },
  { label: '并购融资', value: '并购融资' },
  { label: '技术突破', value: '技术突破' },
  { label: '招投标', value: '招投标' },
  { label: '其他', value: '其他' },
]

// 事件类型标签配色
const EVENT_TYPE_COLORS: Record<string, string> = {
  投产扩产: 'green',
  停产检修: 'volcano',
  价格变动: 'gold',
  政策发布: 'blue',
  安全事故: 'red',
  环保处罚: 'magenta',
  并购融资: 'purple',
  技术突破: 'cyan',
  招投标: 'geekblue',
  其他: 'default',
}

// 注：影响方向（利好/利空/中性）是投资者视角，产品面向化工从业者，已从界面移除。
// 后端 industry_news.impact_direction 字段与抽取逻辑保留，前端不再展示与筛选。

export default function NewsPage() {
  const navigate = useNavigate()
  const { isLoggedIn } = useSnapshot(authState)
  const { currentIndustryId } = useSnapshot(industryState)
  const currentIndustry = getCurrentIndustry()

  // 资讯状态
  const [newsList, setNewsList] = useState<NewsItem[]>([])
  const [newsLoading, setNewsLoading] = useState(false)
  const [newsCategory, setNewsCategory] = useState('')
  const [newsEventType, setNewsEventType] = useState<string | undefined>(undefined)
  const [newsPage, setNewsPage] = useState(1)
  const [newsTotal, setNewsTotal] = useState(0)
  const [newsStats, setNewsStats] = useState<NewsStats | null>(null)

  // 个人屏蔽：只影响自己，不删除共享库（详见 api/news.ts 同名区块注释）
  const [hiddenCount, setHiddenCount] = useState(0)
  const [hiddenOpen, setHiddenOpen] = useState(false)
  const [hiddenList, setHiddenList] = useState<NewsItem[]>([])
  const [hiddenLoading, setHiddenLoading] = useState(false)
  const [hidingId, setHidingId] = useState<string | null>(null)

  // 共享资讯库：本次会话里已收录的资讯（后端幂等，重复点也安全）
  const [libraryIds, setLibraryIds] = useState<Set<string>>(new Set())
  const [librarySavingId, setLibrarySavingId] = useState<string | null>(null)

  // 备注（news_feedback.note）：只有自己可见，会作为 L2a 偏好归纳的输入
  const [noteEditingId, setNoteEditingId] = useState<string | null>(null)
  const [noteDraft, setNoteDraft] = useState('')
  const [noteSaving, setNoteSaving] = useState(false)

  // 碎片检索状态（searchQuery 非空 = 处于检索模式）
  const [searchInput, setSearchInput] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<NewsItem[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [searchMeta, setSearchMeta] = useState<{
    mode: string
    vectorIndexed: boolean
    onlineKept: number
    persisted: number
    foreignFiltered: number
  } | null>(null)
  // 是否同时检索全网（默认开）：一次搜索即可拿到库里没有的最新资讯，并把优质结果沉淀入库
  const [includeOnline, setIncludeOnline] = useState(true)
  // 是否过滤外文结果（默认关）：行业相关性闸对非中文内容放行，打开后可丢弃外文报道
  const [excludeForeign, setExcludeForeign] = useState(false)

  // 每页条数必须是 state：写成常量再传进 Pagination 的 pageSize 时，选中「50条/页」
  // 后受控值立刻被改回 20，表现就是"选完自动跳回 20条/页"。
  const [pageSize, setPageSize] = useState(20)

  // 获取资讯列表
  const fetchNews = useCallback(async () => {
    setNewsLoading(true)
    try {
      const res = await api.news.getNewsList({
        category: newsCategory || undefined,
        event_type: newsEventType,
        industry_id: currentIndustryId,
        limit: pageSize,
        offset: (newsPage - 1) * pageSize,
      })
      if (res.success) {
        setNewsList(res.data)
        setNewsTotal(res.total)
        setNewsStats(res.stats)
        // 后端已按当前用户过滤掉屏蔽过的资讯，这里同步徽标数字
        setHiddenCount(res.hidden_count || 0)
      }
    } catch (error: any) {
      console.error('[NewsPage] fetchNews 错误:', error)
      message.error(error?.response?.data?.detail || '获取资讯失败')
    } finally {
      setNewsLoading(false)
    }
  }, [newsCategory, newsEventType, newsPage, pageSize, currentIndustryId])

  useEffect(() => {
    if (isLoggedIn) {
      fetchNews()
    }
  }, [isLoggedIn, fetchNews])

  // 碎片检索：输入公司/产品/工艺/政策片段 -> 相关报道
  const doSearch = useCallback(
    async (raw: string) => {
      const q = raw.trim()
      if (!q) {
        // 清空即退出检索模式，回到常规列表
        setSearchQuery('')
        setSearchResults([])
        setSearchMeta(null)
        return
      }
      setSearchQuery(q)
      setSearchLoading(true)
      try {
        const res = await api.news.searchNews({
          query: q,
          industry_id: currentIndustryId,
          limit: 20,
          include_online: includeOnline,
          exclude_foreign: excludeForeign,
        })
        setSearchResults(res.data)
        setSearchMeta({
          mode: res.mode,
          vectorIndexed: res.vector_indexed,
          onlineKept: res.online_kept || 0,
          persisted: res.persisted || 0,
          foreignFiltered: res.foreign_filtered || 0,
        })
        // 有全网结果沉淀入库时，顺带刷新列表页码总数
        if ((res.persisted || 0) > 0) {
          fetchNews()
        }
      } catch (error: any) {
        console.error('[NewsPage] doSearch 错误:', error)
        message.error(error?.response?.data?.detail || '检索失败')
        setSearchResults([])
      } finally {
        setSearchLoading(false)
      }
    },
    [currentIndustryId, includeOnline, excludeForeign, fetchNews],
  )

  const exitSearch = () => {
    setSearchInput('')
    setSearchQuery('')
    setSearchResults([])
    setSearchMeta(null)
  }

  // 首页检索入口会带 ?q=xxx 跳进来：这里自动发起一次检索，并把参数从地址栏清掉
  // （清掉是为了刷新 / 后退时不再重复触发，也避免和「返回列表」的行为打架）
  const [searchParams, setSearchParams] = useSearchParams()
  const bootQueryRef = useRef<string | null>(null)
  useEffect(() => {
    const q = searchParams.get('q')?.trim()
    if (!q || bootQueryRef.current === q) return
    bootQueryRef.current = q
    setSearchInput(q)
    doSearch(q)
    const next = new URLSearchParams(searchParams)
    next.delete('q')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams, doSearch])

  // 格式化时间
  const formatTime = (timeStr: string) => {
    if (!timeStr) return '-'
    const date = new Date(timeStr)
    return date.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    })
  }

  // 打开链接
  const openLink = (url: string) => {
    if (url) {
      window.open(url, '_blank')
    }
  }

  // 资讯卡片（常规列表与检索结果共用；showScore 时额外展示相关度与命中理由）
  const renderNewsItem = (item: NewsItem, showScore = false) => (
    <div
      key={item.id}
      className={styles['news-item']}
      onClick={(e) => {
        // 整个卡片可点开原文。但 Popover 内容是用 Portal 渲染到 body 的：DOM 上不在卡片里，
        // React 合成事件却仍会沿「组件树」冒泡到卡片（这是 createPortal 的既定行为），
        // 结果点备注框里的输入框会把原文开出来。用 currentTarget.contains 判定真实 DOM 归属。
        if (!e.currentTarget.contains(e.target as Node)) return
        openLink(item.source_url)
      }}
    >
      <div className={styles['news-title']}>{item.title}</div>
      {showScore && item.score !== undefined && (
        <div className={styles['news-tags']}>
          <Tag color="green">相关度 {item.score.toFixed(3)}</Tag>
          {item.source_type === 'web' ? (
            <Tag color="cyan">全网</Tag>
          ) : item.source_type === 'library' ? (
            <Tag bordered={false}>库内</Tag>
          ) : null}
          {item.match_reason && <Tag bordered={false}>{item.match_reason}</Tag>}
        </div>
      )}
      {/* 事件化标签：事件类型 + 关键数字 */}
      {(item.event_type || item.key_numbers?.length) && (
        <div className={styles['news-tags']}>
          {item.event_type && (
            <Tag color={EVENT_TYPE_COLORS[item.event_type] || 'default'}>{item.event_type}</Tag>
          )}
          {item.key_numbers?.slice(0, 4).map((n, i) => (
            <Tag key={i} bordered={false}>
              {n.name}
              {n.value}
              {n.unit || ''}
            </Tag>
          ))}
        </div>
      )}
      {(item.summary || item.content) && (
        <div className={styles['news-content']}>{item.summary || item.content}</div>
      )}
      <div className={styles['news-meta']}>
        <span className={styles['meta-item']}>
          <ClockCircleOutlined />
          {formatTime(item.publish_time || item.collected_at)}
        </span>
        {item.department && (
          <span className={styles['meta-item']}>
            <BankOutlined />
            {item.department}
          </span>
        )}
        <span className={styles['meta-item']}>
          <LinkOutlined />
          {item.source || '未知来源'}
        </span>
        <Tag
          color={
            item.category === '政策' ? 'blue' : item.category === '研报' ? 'purple' : 'default'
          }
        >
          {item.category || '新闻'}
        </Tag>
        {item.matched_topics?.map((topic) => (
          <Tag color="geekblue" key={topic}>
            {topic}
          </Tag>
        ))}
        <span className={styles['feedback-actions']}>
          <Button
            size="small"
            type={item.feedback?.interested === true ? 'primary' : 'default'}
            icon={<LikeOutlined />}
            onClick={(e) => handleFeedback(e, item, { interested: true })}
          >
            感兴趣
          </Button>
          <Button
            size="small"
            danger={item.feedback?.interested === false}
            icon={<DislikeOutlined />}
            onClick={(e) => handleFeedback(e, item, { interested: false })}
          >
            不感兴趣
          </Button>
          <Button
            size="small"
            type={item.feedback?.relevant === true ? 'primary' : 'default'}
            icon={<CheckCircleOutlined />}
            onClick={(e) => handleFeedback(e, item, { relevant: true })}
          >
            相关
          </Button>
          <Button
            size="small"
            danger={item.feedback?.relevant === false}
            icon={<CloseCircleOutlined />}
            onClick={(e) => handleFeedback(e, item, { relevant: false })}
          >
            不相关
          </Button>
          {/* 备注：写给自己看的判断依据，只有自己可见（后端按 user_id 隔离），会作为偏好归纳的输入 */}
          {item.id && (
            <Popover
              trigger="click"
              placement="topRight"
              open={noteEditingId === item.id}
              onOpenChange={(open) => handleNoteOpenChange(open, item)}
              content={
                // stopPropagation 双保险：即使哪天卡片那边的判断被改掉，
                // 点备注框也不会通过 Portal 冒泡触发卡片的「打开原文」
                <div className={styles['note-editor']} onClick={(e) => e.stopPropagation()}>
                  <div className={styles['note-editor-tip']}>
                    备注只有你自己可见，会用于优化你的推荐
                  </div>
                  <Input.TextArea
                    autoFocus
                    rows={3}
                    maxLength={2000}
                    showCount
                    value={noteDraft}
                    placeholder="例如：与我在跟的 PA66 技改相关，重点关注产能与开工率"
                    onChange={(e) => setNoteDraft(e.target.value)}
                  />
                  <div className={styles['note-editor-actions']}>
                    <Button size="small" onClick={() => setNoteEditingId(null)}>
                      取消
                    </Button>
                    {item.feedback?.note ? (
                      <Button
                        size="small"
                        danger
                        loading={noteSaving}
                        onClick={() => saveNote(item, '')}
                      >
                        清除
                      </Button>
                    ) : null}
                    <Button
                      size="small"
                      type="primary"
                      loading={noteSaving}
                      onClick={() => saveNote(item, noteDraft)}
                    >
                      保存
                    </Button>
                  </div>
                </div>
              }
            >
              <Button
                size="small"
                type={item.feedback?.note ? 'primary' : 'default'}
                icon={<EditOutlined />}
                onClick={(e) => e.stopPropagation()}
              >
                备注
              </Button>
            </Popover>
          )}
          {/* 收录到共享资讯库：所有人可见、可批注；原文只读（协作发生在 /library 的批注里） */}
          {item.id && (
            libraryIds.has(item.id) ? (
              <Button
                size="small"
                type="primary"
                ghost
                icon={<BookOutlined />}
                onClick={(e) => {
                  e.stopPropagation()
                  navigate(`/library?news=${item.id}`)
                }}
              >
                已在共享库
              </Button>
            ) : (
              <Button
                size="small"
                icon={<BookOutlined />}
                loading={librarySavingId === item.id}
                onClick={(e) => handleAddToLibrary(e, item)}
              >
                收录
              </Button>
            )
          )}
          {/* 个人屏蔽：只让自己看不到，不删除共享库、不影响他人（可随时恢复） */}
          {item.id && (
            <Button
              size="small"
              icon={<EyeInvisibleOutlined />}
              loading={hidingId === item.id}
              onClick={(e) => handleHide(e, item)}
            >
              屏蔽
            </Button>
          )}
        </span>
      </div>
      {/* 我的备注：只有自己可见；点一下即可修改 */}
      {item.feedback?.note ? (
        <div
          className={styles['news-note']}
          title="点击修改备注"
          onClick={(e) => {
            e.stopPropagation()
            openNote(item)
          }}
        >
          <EditOutlined />
          <span className={styles['news-note-text']}>{item.feedback.note}</span>
        </div>
      ) : null}
    </div>
  )

  const handleFeedback = async (
    event: React.MouseEvent,
    item: NewsItem,
    payload: { interested?: boolean; relevant?: boolean },
  ) => {
    event.stopPropagation()
    try {
      const result = await api.news.submitNewsFeedback(item.id, payload)
      await fetchNews()
      const created = result?.created_topics ?? []
      const retired = result?.retired_topics ?? []
      if (created.length) {
        // L1.5：没有任何主题命中时，系统从这条资讯里自动派生候选主题
        message.success({
          content: `已自动为你新增关注主题：${created.map((topic) => topic.name).join('、')}，可在「我的关注」确认或删除`,
          duration: 4,
        })
      } else if (retired.length) {
        message.info(`已自动停用不再需要的关注主题：${retired.map((topic) => topic.name).join('、')}`)
      } else {
        message.success('反馈已记录，将用于优化推荐')
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '反馈保存失败')
    }
  }

  // ---- 备注（news_feedback.note）----
  // 只有自己可见：后端按 user_id 隔离；保存后还会作为 L2a 偏好归纳的输入（截 80 字喂模型）。
  const openNote = (item: NewsItem) => {
    setNoteEditingId(item.id)
    setNoteDraft(item.feedback?.note || '')
  }

  const handleNoteOpenChange = (open: boolean, item: NewsItem) => {
    if (open) {
      openNote(item)
    } else {
      setNoteEditingId((prev) => (prev === item.id ? null : prev))
    }
  }

  // 本地乐观更新「列表」与「检索结果」两张列表，避免整页重拉导致骨架屏闪烁
  const applyNoteLocally = (item: NewsItem, note: string) => {
    const patch = (n: NewsItem): NewsItem =>
      n.id === item.id
        ? {
            ...n,
            feedback: n.feedback
              ? { ...n.feedback, note }
              : { interested: null, relevant: null, note },
          }
        : n
    setNewsList((prev) => prev.map(patch))
    setSearchResults((prev) => prev.map(patch))
  }

  // raw 传 '' 即为清除备注
  const saveNote = async (item: NewsItem, raw: string) => {
    if (!item.id) return
    const note = raw.trim()
    if (note === (item.feedback?.note || '')) {
      setNoteEditingId(null)
      return
    }
    // 后端要求 interested / relevant / note 至少给一项：清空备注时若这条没有任何按钮反馈会被拒
    const hasSignal = item.feedback?.interested != null || item.feedback?.relevant != null
    if (!note && !hasSignal) {
      message.warning('备注不能为空；如果这条你不想再看到，可以用右侧的「屏蔽」')
      return
    }
    setNoteSaving(true)
    try {
      await api.news.submitNewsFeedback(item.id, { note })
      applyNoteLocally(item, note)
      setNoteEditingId(null)
      message.success(note ? '备注已保存（只有你自己可见）' : '备注已清除')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '备注保存失败')
    } finally {
      setNoteSaving(false)
    }
  }

  // 屏蔽一条资讯：本地先剔除 + 同步徽标，不重新拉列表（避免闪骨架屏）
  const handleHide = async (event: React.MouseEvent, item: NewsItem) => {
    event.stopPropagation()
    if (!item.id) return
    setHidingId(item.id)
    try {
      const res = await api.news.hideNews(item.id)
      const wasInList = newsList.some((n) => n.id === item.id)
      setNewsList((prev) => prev.filter((n) => n.id !== item.id))
      setSearchResults((prev) => prev.filter((n) => n.id !== item.id))
      if (wasInList) setNewsTotal((prev) => Math.max(0, prev - 1))
      setHiddenCount(res.hidden_count ?? 0)
      // 弱负信号：屏蔽会顺带小幅降低命中主题的推荐度（不改权重、可随恢复撤销）
      const weakNames = (res.weak_topics || []).map((w) => w.name).filter(Boolean)
      message.success({
        content: weakNames.length
          ? `已屏蔽，并降低了「${weakNames.join('、')}」的推荐度；可在「已屏蔽」里恢复`
          : '已屏蔽，这条资讯不再出现在你的列表与检索结果中；可在「已屏蔽」里恢复',
        duration: 3,
      })
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '屏蔽失败')
    } finally {
      setHidingId(null)
    }
  }

  // 收录到共享资讯库：所有人可见、可批注；原文只读，下架是软删
  const handleAddToLibrary = async (event: React.MouseEvent, item: NewsItem) => {
    event.stopPropagation()
    if (!item.id) return
    setLibrarySavingId(item.id)
    try {
      const res = await api.news.addToLibrary(item.id)
      setLibraryIds((prev) => new Set(prev).add(item.id as string))
      message.success(res.created ? '已收录到共享资讯库' : '这条已经在共享库里了')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '收录失败')
    } finally {
      setLibrarySavingId(null)
    }
  }

  // 拉取「已屏蔽」列表（打开抽屉时调用）
  const loadHidden = useCallback(async () => {
    setHiddenLoading(true)
    try {
      const res = await api.news.getHiddenNews({ limit: 50 })
      setHiddenList(res.data)
      setHiddenCount(res.total)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取已屏蔽列表失败')
    } finally {
      setHiddenLoading(false)
    }
  }, [])

  // 取消屏蔽：从抽屉移除并刷新列表，让它回到资讯流里
  const handleUnhide = async (newsId: string) => {
    try {
      const res = await api.news.unhideNews(newsId)
      setHiddenList((prev) => prev.filter((n) => n.id !== newsId))
      setHiddenCount(res.hidden_count ?? 0)
      message.success('已恢复显示')
      fetchNews()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '恢复失败')
    }
  }

  const openHidden = () => {
    setHiddenOpen(true)
    loadHidden()
  }

  const isSearchMode = searchQuery.trim() !== ''

  if (!isLoggedIn) {
    return (
      <div className={styles['news-page']}>
        <div className={styles['empty-state']}>
          <Empty description="请先登录" />
          <Button type="primary" onClick={() => navigate('/login')}>
            去登录
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className={styles['news-page']}>
      <div className={styles.header}>
        <div className={styles['header-left']}>
          <FileTextOutlined style={{ fontSize: 24, marginRight: 12 }} />
          <h2>行业资讯</h2>
          <Tag color="purple" style={{ marginLeft: 12 }}>{currentIndustry.name}</Tag>
        </div>
        <div className={styles['header-right']}>
          <Button
            icon={<EyeInvisibleOutlined />}
            onClick={openHidden}
            title="查看并恢复你屏蔽过的资讯（只影响你自己）"
          >
            已屏蔽{hiddenCount > 0 ? ` (${hiddenCount})` : ''}
          </Button>
        </div>
      </div>

      {/* 碎片检索：输入公司/产品/工艺/政策片段，得到相关报道 */}
      <Card size="small" style={{ marginBottom: 16 }} className={styles['search-card']}>
        <Input.Search
          size="large"
          allowClear
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          onSearch={doSearch}
          enterButton="检索"
          loading={searchLoading}
          placeholder="输入公司 / 产品 / 工艺 / 政策片段… 例如：万华化学 MDI 扩产、危化品监管新规"
        />
        <div className={styles['search-toolbar']}>
          <span className={styles['search-toolbar-group']}>
            <Switch
              size="small"
              checked={includeOnline}
              onChange={setIncludeOnline}
              disabled={searchLoading}
            />
            <span className={styles['search-toolbar-label']}>同时搜全网</span>
            <span
              className={styles['search-toolbar-hint']}
              title="开启后会实时检索全网并把优质结果沉淀入库，单次约 5~15 秒；关闭则只搜库内，通常 1 秒内返回"
            >
              {includeOnline ? '开启中：会实时拉全网，约 5~15 秒' : '已关闭：只搜库内，更快'}
            </span>
          </span>
          <span className={styles['search-toolbar-divider']} />
          <span className={styles['search-toolbar-group']}>
            <Switch
              size="small"
              checked={excludeForeign}
              onChange={setExcludeForeign}
              disabled={searchLoading}
            />
            <span className={styles['search-toolbar-label']}>过滤外文</span>
            <span
              className={styles['search-toolbar-hint']}
              title="开启后丢弃标题为外文的报道（如俄文、德文来源）。默认关闭，外文结果会正常展示"
            >
              {excludeForeign ? '已开启：只看中文报道' : '已关闭：外文结果也会展示'}
            </span>
          </span>
        </div>
        {searchQuery && searchMeta && (
          <div className={styles['search-summary']}>
            <span>
              关键词「<strong>{searchQuery}</strong>」找到 <strong>{searchResults.length}</strong> 条相关报道
            </span>
            <Tag color={searchMeta.mode === 'rerank' ? 'green' : 'blue'}>
              {searchMeta.mode === 'rerank'
                ? '语义 + 关键词重排'
                : searchMeta.mode === 'hybrid'
                ? '混合检索'
                : '关键词检索'}
            </Tag>
            {!searchMeta.vectorIndexed && <Tag color="orange">向量索引未就绪，已降级为关键词</Tag>}
            {searchMeta.foreignFiltered > 0 && (
              <Tag bordered={false}>已过滤外文 {searchMeta.foreignFiltered} 条</Tag>
            )}
            <Button type="link" size="small" onClick={exitSearch}>
              返回列表
            </Button>
          </div>
        )}
      </Card>

      <Card>
        {/* 统计信息（检索模式下隐藏，聚焦结果） */}
        {!isSearchMode && (
        <Row gutter={16} style={{ marginBottom: 24 }}>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="资讯总数"
                value={newsStats?.total || 0}
                prefix={<FileTextOutlined />}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="24小时更新"
                value={newsStats?.recent_24h || 0}
                valueStyle={{ color: '#3f8600' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="政策"
                value={newsStats?.by_category?.['政策'] || 0}
                valueStyle={{ color: 'var(--ant-color-primary)' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="研报"
                value={newsStats?.by_category?.['研报'] || 0}
                valueStyle={{ color: '#722ed1' }}
              />
            </Card>
          </Col>
        </Row>
        )}

        {/* 分类筛选（检索模式下隐藏） */}
        {!isSearchMode && (
        <div className={styles.filters}>
          <div className={styles['category-tags']}>
            {NEWS_CATEGORIES.map((cat) => (
              <span
                key={cat.value}
                className={`${styles['category-tag']} ${
                  newsCategory === cat.value ? styles.active : ''
                }`}
                onClick={() => {
                  setNewsCategory(cat.value)
                  setNewsPage(1)
                }}
              >
                {cat.label}
              </span>
            ))}
          </div>
          <div className={styles['event-filters']}>
            <Select
              allowClear
              placeholder="事件类型"
              style={{ width: 150 }}
              value={newsEventType}
              options={EVENT_TYPE_OPTIONS}
              onChange={(value) => {
                setNewsEventType(value)
                setNewsPage(1)
              }}
            />
          </div>
        </div>
        )}

        {/* 检索结果 / 资讯列表 */}
        {isSearchMode ? (
          searchLoading ? (
            <div className={styles['news-list']}>
              {[1, 2, 3, 4, 5].map((i) => (
                <div key={i} className={styles['news-item']}>
                  <Skeleton active paragraph={{ rows: 3 }} />
                </div>
              ))}
            </div>
          ) : searchResults.length === 0 ? (
            <Empty description={`没有找到与「${searchQuery}」相关的报道`} />
          ) : (
            <div className={styles['news-list']}>
              {searchResults.map((item) => renderNewsItem(item, true))}
            </div>
          )
        ) : newsLoading ? (
          <div className={styles['news-list']}>
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className={styles['news-item']}>
                <Skeleton active paragraph={{ rows: 3 }} />
              </div>
            ))}
          </div>
        ) : newsList.length === 0 ? (
          <Empty description="暂无资讯" />
        ) : (
          <div className={styles['news-list']}>
            {newsList.map((item) => renderNewsItem(item))}
          </div>
        )}

        {/* 分页（检索模式无分页）；阈值用最小可选条数 20，换到 50/100 时不会被收起 */}
        {!isSearchMode && newsTotal > 20 && (
          <div className={styles['pagination-wrapper']}>
            <Pagination
              current={newsPage}
              pageSize={pageSize}
              total={newsTotal}
              showSizeChanger
              pageSizeOptions={[20, 50, 100]}
              onChange={(page, size) => {
                // 换每页条数后回到第 1 页，否则 offset 会落到一个空页
                if (size !== pageSize) {
                  setPageSize(size)
                  setNewsPage(1)
                  return
                }
                setNewsPage(page)
              }}
              showTotal={(total) => `共 ${total} 条`}
            />
          </div>
        )}
      </Card>

      {/* 已屏蔽：只影响自己，不删共享库、不影响其他同事 */}
      <Drawer
        title="已屏蔽的资讯"
        open={hiddenOpen}
        onClose={() => setHiddenOpen(false)}
        width={560}
      >
        <div className={styles['hidden-hint']}>
          屏蔽只影响你自己：这些资讯不再出现在你的资讯列表与检索结果中，
          共享库与其他同事不受影响，随时可以恢复。
        </div>
        {hiddenLoading ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : hiddenList.length === 0 ? (
          <Empty description="还没有屏蔽任何资讯" />
        ) : (
          <div className={styles['hidden-list']}>
            {hiddenList.map((item) => (
              <div key={item.id} className={styles['hidden-item']}>
                <div className={styles['hidden-item-main']}>
                  <div className={styles['hidden-item-title']}>{item.title}</div>
                  <div className={styles['hidden-item-meta']}>
                    <span>{item.source || '未知来源'}</span>
                    <span>{formatTime(item.publish_time || item.collected_at)}</span>
                  </div>
                  {(item.weak_topics?.length ?? 0) > 0 && (
                    <div className={styles['hidden-item-weak']}>
                      屏蔽时降低了「
                      {item.weak_topics!.map((w) => w.name).join('、')}
                      」的推荐度（恢复即撤销）
                    </div>
                  )}
                </div>
                <Button
                  size="small"
                  icon={<UndoOutlined />}
                  onClick={() => handleUnhide(item.id)}
                >
                  恢复显示
                </Button>
              </div>
            ))}
          </div>
        )}
      </Drawer>
    </div>
  )
}
