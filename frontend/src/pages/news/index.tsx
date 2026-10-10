import { useEffect, useState, useCallback } from 'react'
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
} from '@ant-design/icons'
import { useSnapshot } from 'valtio'
import { authState } from '@/store/auth'
import { industryState, getCurrentIndustry } from '@/store/industry'
import { useNavigate } from 'react-router-dom'
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

  const pageSize = 20

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
      }
    } catch (error: any) {
      console.error('[NewsPage] fetchNews 错误:', error)
      message.error(error?.response?.data?.detail || '获取资讯失败')
    } finally {
      setNewsLoading(false)
    }
  }, [newsCategory, newsEventType, newsPage, currentIndustryId])

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
      onClick={() => openLink(item.source_url)}
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
        </span>
      </div>
    </div>
  )

  const handleFeedback = async (
    event: React.MouseEvent,
    item: NewsItem,
    payload: { interested?: boolean; relevant?: boolean },
  ) => {
    event.stopPropagation()
    try {
      await api.news.submitNewsFeedback(item.id, payload)
      await fetchNews()
      message.success('反馈已记录，将用于优化推荐')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '反馈保存失败')
    }
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
                valueStyle={{ color: '#1890ff' }}
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

        {/* 分页（检索模式无分页） */}
        {!isSearchMode && newsTotal > pageSize && (
          <div className={styles['pagination-wrapper']}>
            <Pagination
              current={newsPage}
              pageSize={pageSize}
              total={newsTotal}
              onChange={(page) => setNewsPage(page)}
              showTotal={(total) => `共 ${total} 条`}
            />
          </div>
        )}
      </Card>
    </div>
  )
}
