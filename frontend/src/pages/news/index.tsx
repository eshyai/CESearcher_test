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
  Space,
} from 'antd'
import {
  FileTextOutlined,
  ReloadOutlined,
  ClockCircleOutlined,
  BankOutlined,
  LinkOutlined,
  SyncOutlined,
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
import type { FocusTopic, NewsItem, NewsStats } from '@/api/news'
import CollectionModal, { CollectionResult } from '@/components/collection-modal'
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

// 影响方向：按国内习惯，利好=红、利空=绿、中性=灰
const IMPACT_META: Record<string, { label: string; color: string }> = {
  positive: { label: '利好', color: '#cf1322' },
  negative: { label: '利空', color: '#389e0d' },
  neutral: { label: '中性', color: '#8c8c8c' },
}

const IMPACT_OPTIONS = [
  { label: '利好', value: 'positive' },
  { label: '利空', value: 'negative' },
  { label: '中性', value: 'neutral' },
]

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
  const [newsImpact, setNewsImpact] = useState<string | undefined>(undefined)
  const [newsPage, setNewsPage] = useState(1)
  const [newsTotal, setNewsTotal] = useState(0)
  const [newsStats, setNewsStats] = useState<NewsStats | null>(null)
  const [focusTopics, setFocusTopics] = useState<FocusTopic[]>([])
  const [topicName, setTopicName] = useState('')
  const [topicType, setTopicType] = useState<FocusTopic['topic_type']>('industry')

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
  } | null>(null)
  // 是否同时检索全网（默认开）：一次搜索即可拿到库里没有的最新资讯，并把优质结果沉淀入库
  const [includeOnline, setIncludeOnline] = useState(true)

  // 采集状态
  const [collecting, setCollecting] = useState(false)
  const [collectionModalOpen, setCollectionModalOpen] = useState(false)
  const [collectionResult, setCollectionResult] = useState<CollectionResult | null>(null)

  const pageSize = 20

  // 获取资讯列表
  const fetchNews = useCallback(async () => {
    setNewsLoading(true)
    try {
      const res = await api.news.getNewsList({
        category: newsCategory || undefined,
        event_type: newsEventType,
        impact_direction: newsImpact,
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
  }, [newsCategory, newsEventType, newsImpact, newsPage, currentIndustryId])

  useEffect(() => {
    if (isLoggedIn) {
      fetchNews()
      api.news.getFocusTopics().then(setFocusTopics).catch(() => undefined)
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
        })
        setSearchResults(res.data)
        setSearchMeta({
          mode: res.mode,
          vectorIndexed: res.vector_indexed,
          onlineKept: res.online_kept || 0,
          persisted: res.persisted || 0,
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
    [currentIndustryId, includeOnline, fetchNews],
  )

  const exitSearch = () => {
    setSearchInput('')
    setSearchQuery('')
    setSearchResults([])
    setSearchMeta(null)
  }

  const handleCreateTopic = async () => {
    const name = topicName.trim()
    if (!name) return
    try {
      const topic = await api.news.createFocusTopic({ topic_type: topicType, name, query_text: name })
      setFocusTopics((items) => [topic, ...items])
      setTopicName('')
      message.success('关注主题已添加')
      await fetchNews()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '添加关注主题失败')
    }
  }

  // 手动触发采集
  const handleCollect = async () => {
    // 打开模态框
    setCollectionModalOpen(true)
    setCollectionResult(null)
    setCollecting(true)

    try {
      // 手动采集按"小批快返"设计：LLM 抽取吞吐实测约 7s/条（端点限流，
      // 加并发收益很小），24 条约 170s + 采集 35s，能稳定落在 300s 超时内。
      // 需要更大批量请走 worker 定时任务（python app/worker_main.py --once）。
      const res = await api.news.triggerCollection({
        max_news: 30,
        max_bidding: 20,
        industry_id: currentIndustryId,
      })

      // 设置结果
      setCollectionResult(res)

      // 刷新数据
      if (res.success) {
        await fetchNews()
      }
    } catch (error: any) {
      console.error('[NewsPage] handleCollect 错误:', error)
      setCollectionResult({
        success: false,
        message: '采集失败',
        news_collected: 0,
        bidding_collected: 0,
        errors: [error?.message || '网络请求失败'],
      })
    } finally {
      setCollecting(false)
    }
  }

  // 关闭模态框
  const handleCloseModal = () => {
    if (!collecting) {
      setCollectionModalOpen(false)
      setCollectionResult(null)
    }
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
      {/* 事件化标签：事件类型 + 影响方向 + 关键数字 */}
      {(item.event_type || item.impact_direction || item.key_numbers?.length) && (
        <div className={styles['news-tags']}>
          {item.event_type && (
            <Tag color={EVENT_TYPE_COLORS[item.event_type] || 'default'}>{item.event_type}</Tag>
          )}
          {item.impact_direction && IMPACT_META[item.impact_direction] && (
            <Tag color={IMPACT_META[item.impact_direction].color}>
              {IMPACT_META[item.impact_direction].label}
            </Tag>
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
      const result = await api.news.submitNewsFeedback(item.id, payload) as { updated_topics?: FocusTopic[] }
      if (result.updated_topics?.length) {
        const updates = new Map(result.updated_topics.map((topic) => [topic.id, topic]))
        setFocusTopics((items) => items.map((topic) => updates.get(topic.id) || topic))
      }
      await fetchNews()
      message.success('反馈已记录，关注权重已更新')
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
        <div className={styles['header-right']}>
          <Button
            type="primary"
            icon={collecting ? <SyncOutlined spin /> : <ReloadOutlined />}
            onClick={handleCollect}
            disabled={collecting}
          >
            {collecting ? '采集中...' : '立即采集'}
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
            <Button type="link" size="small" onClick={exitSearch}>
              返回列表
            </Button>
          </div>
        )}
      </Card>

      <Card size="small" style={{ marginBottom: 16 }} title="关注主题">
        <Space wrap>
          <Select value={topicType} onChange={setTopicType} style={{ width: 110 }} options={[
            { value: 'industry', label: '行业' }, { value: 'customer', label: '客户' },
            { value: 'process', label: '工艺' }, { value: 'technology', label: '技术' },
            { value: 'product', label: '产品' },
          ]} />
          <Input value={topicName} onChange={(e) => setTopicName(e.target.value)}
            onPressEnter={handleCreateTopic} placeholder="例如：HPPO、某客户、废塑料化学回收" style={{ width: 320 }} />
          <Button type="primary" onClick={handleCreateTopic}>添加关注</Button>
          {focusTopics.map((topic) => (
            <Tag key={topic.id} color="blue">{topic.name} · {topic.weight.toFixed(2)}</Tag>
          ))}
        </Space>
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
            <Select
              allowClear
              placeholder="影响方向"
              style={{ width: 130 }}
              value={newsImpact}
              options={IMPACT_OPTIONS}
              onChange={(value) => {
                setNewsImpact(value)
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

      {/* 采集进度模态框 */}
      <CollectionModal
        open={collectionModalOpen}
        collecting={collecting}
        result={collectionResult}
        industryName={currentIndustry.name}
        onClose={handleCloseModal}
      />
    </div>
  )
}
