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

export default function NewsPage() {
  const navigate = useNavigate()
  const { isLoggedIn } = useSnapshot(authState)
  const { currentIndustryId } = useSnapshot(industryState)
  const currentIndustry = getCurrentIndustry()

  // 资讯状态
  const [newsList, setNewsList] = useState<NewsItem[]>([])
  const [newsLoading, setNewsLoading] = useState(false)
  const [newsCategory, setNewsCategory] = useState('')
  const [newsPage, setNewsPage] = useState(1)
  const [newsTotal, setNewsTotal] = useState(0)
  const [newsStats, setNewsStats] = useState<NewsStats | null>(null)
  const [focusTopics, setFocusTopics] = useState<FocusTopic[]>([])
  const [topicName, setTopicName] = useState('')
  const [topicType, setTopicType] = useState<FocusTopic['topic_type']>('industry')

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
  }, [newsCategory, newsPage, currentIndustryId])

  useEffect(() => {
    if (isLoggedIn) {
      fetchNews()
      api.news.getFocusTopics().then(setFocusTopics).catch(() => undefined)
    }
  }, [isLoggedIn, fetchNews])

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
      const res = await api.news.triggerCollection({
        max_news: 50,
        max_bidding: 50,
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
        {/* 统计信息 */}
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

        {/* 分类筛选 */}
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
        </div>

        {/* 资讯列表 */}
        {newsLoading ? (
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
            {newsList.map((item) => (
              <div
                key={item.id}
                className={styles['news-item']}
                onClick={() => openLink(item.source_url)}
              >
                <div className={styles['news-title']}>
                  {item.title}
                </div>
                {item.content && (
                  <div className={styles['news-content']}>{item.content}</div>
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
                      item.category === '政策'
                        ? 'blue'
                        : item.category === '研报'
                        ? 'purple'
                        : 'default'
                    }
                  >
                    {item.category || '新闻'}
                  </Tag>
                  {item.matched_topics?.map((topic) => (
                    <Tag color="geekblue" key={topic}>{topic}</Tag>
                  ))}
                  <span className={styles['feedback-actions']}>
                    <Button size="small" type={item.feedback?.interested === true ? 'primary' : 'default'}
                      icon={<LikeOutlined />} onClick={(e) => handleFeedback(e, item, { interested: true })}>感兴趣</Button>
                    <Button size="small" danger={item.feedback?.interested === false}
                      icon={<DislikeOutlined />} onClick={(e) => handleFeedback(e, item, { interested: false })}>不感兴趣</Button>
                    <Button size="small" type={item.feedback?.relevant === true ? 'primary' : 'default'}
                      icon={<CheckCircleOutlined />} onClick={(e) => handleFeedback(e, item, { relevant: true })}>相关</Button>
                    <Button size="small" danger={item.feedback?.relevant === false}
                      icon={<CloseCircleOutlined />} onClick={(e) => handleFeedback(e, item, { relevant: false })}>不相关</Button>
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 分页 */}
        {newsTotal > pageSize && (
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
