import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Card,
  Button,
  Tag,
  Table,
  Modal,
  Form,
  Input,
  Select,
  InputNumber,
  Space,
  message,
  Progress,
  Popconfirm,
  Alert,
  Segmented,
  Tooltip,
  Empty,
  Badge,
  Drawer,
  List,
  Divider,
  Spin,
  Typography,
} from 'antd'
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  StarOutlined,
  ThunderboltOutlined,
  CheckOutlined,
  CloseOutlined,
  HistoryOutlined,
  RobotOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import * as api from '@/api'
import type {
  FocusTopic,
  PreferenceSuggestion,
  FocusTopicRevision,
  InductionStatus,
  SuggestionKind,
} from '@/api/news'
import styles from './index.module.scss'

// 主题类型元信息
const TOPIC_TYPE_META: Record<FocusTopic['topic_type'], { label: string; color: string }> = {
  industry: { label: '行业', color: 'blue' },
  customer: { label: '客户', color: 'purple' },
  process: { label: '工艺', color: 'cyan' },
  technology: { label: '技术', color: 'geekblue' },
  product: { label: '产品', color: 'gold' },
}

const TOPIC_TYPE_OPTIONS = Object.entries(TOPIC_TYPE_META).map(([value, meta]) => ({
  value,
  label: meta.label,
}))

// L2a 建议类型元信息
const SUGGESTION_META: Record<
  SuggestionKind,
  { label: string; color: string; accept: string; soon: string }
> = {
  add: { label: '新增关注', color: 'green', accept: '采纳并新增', soon: '新增中' },
  adjust: { label: '调整权重', color: 'blue', accept: '采纳调整', soon: '调整中' },
  merge: { label: '合并同义', color: 'purple', accept: '采纳合并', soon: '合并中' },
  drop: { label: '建议停用', color: 'volcano', accept: '采纳停用', soon: '停用中' },
  insight: { label: '画像结论', color: 'gold', accept: '知道了', soon: '处理中' },
}

type SourceFilter = 'all' | 'manual' | 'auto'

// 把建议的 payload 翻成人话，让用户能预判「采纳后会发生什么」
function describeSuggestion(item: PreferenceSuggestion): string {
  const p = item.payload || {}
  switch (item.kind) {
    case 'add': {
      const type = TOPIC_TYPE_META[p.topic_type as FocusTopic['topic_type']]?.label || p.topic_type
      const weight = typeof p.weight === 'number' ? p.weight.toFixed(2) : '1.00'
      return `新增关注主题「${item.name}」（类型：${type || '未定'}，初始权重 ${weight}）`
    }
    case 'adjust': {
      const from = Number(p.from_weight ?? 0)
      const delta = Number(p.delta ?? 0)
      const to = Math.max(-2, Math.min(2, from + delta))
      return `「${item.name}」权重 ${from.toFixed(2)} → ${to.toFixed(2)}`
    }
    case 'merge': {
      const targets = Array.isArray(p.targets) ? p.targets : []
      const others = targets.filter((t: string) => t !== p.into)
      return `把「${others.join('、') || '同义写法'}」并入「${p.into || item.name}」，合并后仅保留一条主题（原写法作为别名保留）`
    }
    case 'drop': {
      const from = Number(p.from_weight ?? 0)
      return `停用「${item.name}」（当前权重 ${from.toFixed(2)}，停用后不再参与资讯排序，可随时手动启用）`
    }
    case 'insight':
      return item.name
    default:
      return item.name
  }
}

function revisionKindLabel(kind: string): string {
  return SUGGESTION_META[kind as SuggestionKind]?.label || kind
}

export default function TopicsPage() {
  const [topics, setTopics] = useState<FocusTopic[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<FocusTopic | null>(null)
  const [saving, setSaving] = useState(false)
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all')
  const [form] = Form.useForm()

  // L2a 状态
  const [status, setStatus] = useState<InductionStatus | null>(null)
  const [suggestions, setSuggestions] = useState<PreferenceSuggestion[]>([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)
  const [actingId, setActingId] = useState<string | null>(null)
  const [inducting, setInducting] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [revisions, setRevisions] = useState<FocusTopicRevision[]>([])
  const [revisionsLoading, setRevisionsLoading] = useState(false)

  const fetchTopics = useCallback(async () => {
    setLoading(true)
    try {
      const list = await api.news.getFocusTopics()
      setTopics(list)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取关注主题失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchInduction = useCallback(async () => {
    setSuggestionsLoading(true)
    try {
      const [st, list] = await Promise.all([
        api.news.getInductionStatus(),
        api.news.getSuggestions({ status: 'pending' }),
      ])
      setStatus(st)
      setSuggestions(list)
    } catch (error: any) {
      // 401 已由全局拦截器处理，这里只提示业务错误
      if (error?.response?.status !== 401) {
        message.error(error?.response?.data?.detail || '获取归纳建议失败')
      }
    } finally {
      setSuggestionsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchTopics()
    fetchInduction()
  }, [fetchTopics, fetchInduction])

  const autoCount = topics.filter((topic) => (topic.source || 'manual') === 'auto').length

  const visibleTopics = useMemo(
    () =>
      sourceFilter === 'all'
        ? topics
        : topics.filter((topic) => (topic.source || 'manual') === sourceFilter),
    [topics, sourceFilter],
  )

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ topic_type: 'industry', weight: 1 })
    setModalOpen(true)
  }

  const openEdit = (topic: FocusTopic) => {
    setEditing(topic)
    form.setFieldsValue({
      topic_type: topic.topic_type,
      name: topic.name,
      query_text: topic.query_text,
      weight: topic.weight,
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      if (editing) {
        await api.news.updateFocusTopic(editing.id, {
          query_text: values.query_text,
          weight: values.weight,
        })
        message.success('关注主题已更新')
      } else {
        await api.news.createFocusTopic({
          topic_type: values.topic_type,
          name: values.name,
          query_text: values.query_text,
          weight: values.weight,
        })
        message.success('关注主题已添加')
      }
      setModalOpen(false)
      await fetchTopics()
    } catch (error: any) {
      if (error?.errorFields) return // 表单校验失败
      message.error(error?.response?.data?.detail || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (topic: FocusTopic) => {
    try {
      await api.news.deleteFocusTopic(topic.id)
      message.success('已删除')
      await fetchTopics()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  const handleToggleActive = async (topic: FocusTopic) => {
    try {
      await api.news.updateFocusTopic(topic.id, { is_active: !topic.is_active })
      message.success(topic.is_active ? '已停用' : '已启用')
      await fetchTopics()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '操作失败')
    }
  }

  // 把系统自动派生的候选主题确认为正式关注
  const handleConfirm = async (topic: FocusTopic) => {
    try {
      await api.news.updateFocusTopic(topic.id, { source: 'manual' })
      message.success(`「${topic.name}」已确认为正式关注`)
      await fetchTopics()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '操作失败')
    }
  }

  // ---- L2a：运行归纳 ----
  const handleInduct = async () => {
    setInducting(true)
    try {
      const res = await api.news.runInduction(true)
      if (res?.ok) {
        const n = res.created ?? 0
        if (n > 0) {
          message.success(`归纳完成，产出 ${n} 条待确认建议`)
        } else {
          message.info('归纳完成，没有新的建议（已有建议仍在待确认）')
        }
      } else {
        message.warning(res?.message || '本次归纳没有产出')
      }
      await fetchInduction()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '归纳失败，请稍后重试')
    } finally {
      setInducting(false)
    }
  }

  // ---- L2a：接受 / 拒绝建议 ----
  const handleAccept = async (item: PreferenceSuggestion) => {
    setActingId(item.id)
    try {
      const res = await api.news.acceptSuggestion(item.id)
      message.success(res?.detail || '已采纳')
      await Promise.all([fetchInduction(), fetchTopics()])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '采纳失败')
    } finally {
      setActingId(null)
    }
  }

  const handleReject = async (item: PreferenceSuggestion) => {
    setActingId(item.id)
    try {
      const res = await api.news.rejectSuggestion(item.id)
      message.success(res?.detail || '已忽略')
      await fetchInduction()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '操作失败')
    } finally {
      setActingId(null)
    }
  }

  // ---- L2a：归纳历史与回滚 ----
  const openHistory = async () => {
    setHistoryOpen(true)
    setRevisionsLoading(true)
    try {
      setRevisions(await api.news.getRevisions(30))
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取归纳历史失败')
    } finally {
      setRevisionsLoading(false)
    }
  }

  const handleRollback = async (revision: FocusTopicRevision) => {
    try {
      const res = await api.news.rollbackRevision(revision.id)
      const removed = res?.removed?.length ?? 0
      const restored = res?.restored?.length ?? 0
      message.success(`已回滚：移除 ${removed} 条新增主题，还原 ${restored} 条主题`)
      await Promise.all([openHistory(), fetchInduction(), fetchTopics()])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '回滚失败')
    }
  }

  const columns: ColumnsType<FocusTopic> = [
    {
      title: '关注主题',
      dataIndex: 'name',
      key: 'name',
      render: (_, record) => (
        <div className={styles['topic-cell']}>
          <div className={styles['topic-name']}>
            {record.name}
            {(record.source || 'manual') === 'auto' && (
              <Tooltip title="系统根据你对资讯的反馈自动派生。确认后即为正式关注主题，权重与匹配词都可编辑。">
                <Tag className={styles['auto-tag']} color="orange" icon={<ThunderboltOutlined />}>
                  自动派生
                </Tag>
              </Tooltip>
            )}
          </div>
          <div className={styles['topic-query']}>匹配词：{record.query_text}</div>
        </div>
      ),
    },
    {
      title: '类型',
      dataIndex: 'topic_type',
      key: 'topic_type',
      width: 90,
      render: (type: FocusTopic['topic_type']) => (
        <Tag color={TOPIC_TYPE_META[type]?.color}>{TOPIC_TYPE_META[type]?.label || type}</Tag>
      ),
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 100,
      render: (source: FocusTopic['source']) =>
        (source || 'manual') === 'auto' ? (
          <Tag color="orange">自动派生</Tag>
        ) : (
          <Tag>手动创建</Tag>
        ),
    },
    {
      title: '关注权重',
      dataIndex: 'weight',
      key: 'weight',
      width: 200,
      render: (weight: number, record) => {
        const penalty = Number(record.weak_penalty ?? 0)
        const effective = weight + penalty
        return (
          <div className={styles['weight-cell']}>
            {/* 权重范围 [-2, 2]，归一化到 [0, 100%] 展示；进度条按“有效权重”画
                （屏蔽累积的弱降权只影响个性化排序，不改 weight 本身） */}
            <Progress
              percent={Number((((effective + 2) / 4) * 100).toFixed(0))}
              size="small"
              strokeColor={effective >= 1 ? '#cf1322' : effective >= 0 ? '#991D85' : '#8c8c8c'}
              format={() => effective.toFixed(2)}
            />
            {penalty !== 0 && (
              <Tooltip title={`屏蔽带来的弱降权 ${penalty.toFixed(2)}（仅影响你自己的排序，不改权重与计数；取消屏蔽即撤销）`}>
                <span className={styles['weak-hint']}>
                  原 {weight.toFixed(2)}
                </span>
              </Tooltip>
            )}
          </div>
        )
      },
    },
    {
      title: '反馈',
      key: 'feedback',
      width: 140,
      render: (_, record) => (
        <Space size={4} wrap>
          <Tag color="red">感兴趣 {record.positive_count}</Tag>
          <Tag color="default">不感兴趣 {record.negative_count}</Tag>
          {(record.weak_hits ?? 0) > 0 && (
            <Tooltip title="你屏蔽的资讯命中了该主题的次数。属弱负信号：只微调排序，不改权重与计数。">
              <Tag color="default">弱屏蔽 {record.weak_hits}</Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 90,
      render: (active: boolean) => (
        <Tag color={active ? 'green' : 'default'}>{active ? '启用' : '停用'}</Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 240,
      render: (_, record) => (
        <Space size={4}>
          {(record.source || 'manual') === 'auto' && (
            <Button
              size="small"
              type="primary"
              ghost
              icon={<CheckOutlined />}
              onClick={() => handleConfirm(record)}
            >
              确认关注
            </Button>
          )}
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>
            编辑
          </Button>
          <Button size="small" onClick={() => handleToggleActive(record)}>
            {record.is_active ? '停用' : '启用'}
          </Button>
          <Popconfirm
            title="确认删除该关注主题？"
            description="删除后其历史权重不再参与资讯排序。"
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={() => handleDelete(record)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const pendingCount = suggestions.length
  const statusText = (() => {
    if (!status) return ''
    if (!status.last_inducted_at) {
      return `还没归纳过 · 已有 ${status.pending_feedback} 条反馈（攒到 ${status.threshold} 条会自动归纳）`
    }
    const days = status.days_since_last ?? 0
    return `距上次归纳 ${days} 天 · 新增反馈 ${status.pending_feedback} 条（阈值 ${status.threshold}）`
  })()

  return (
    <div className={styles['topics-page']}>
      <div className={styles.header}>
        <div className={styles['header-left']}>
          <StarOutlined style={{ fontSize: 24, marginRight: 12 }} />
          <h2>我的关注</h2>
          {autoCount > 0 && (
            <Tag color="orange" icon={<ThunderboltOutlined />}>
              {autoCount} 条自动派生
            </Tag>
          )}
        </div>
        <Space>
          <Badge count={pendingCount} size="small" offset={[-4, 4]}>
            <Button icon={<ReloadOutlined />} onClick={fetchInduction}>
              刷新
            </Button>
          </Badge>
          <Button icon={<HistoryOutlined />} onClick={openHistory}>
            归纳历史
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增关注
          </Button>
        </Space>
      </div>

      <Alert
        className={styles.tip}
        type="info"
        showIcon
        message="AI 如何自我成长"
        description={
          <>
            在资讯页点击「感兴趣 / 不感兴趣」后，系统会做两件事：① 按有界规则更新相关主题权重——
            新权重 = clamp(0.85 × 旧权重 + 0.35 × 反馈, -2, 2)，权重越高的主题，相关资讯排序越靠前；
            ② 若这条资讯没有任何主题命中，则从资讯内容里
            <strong>自动派生候选关注主题</strong>（标为「自动派生」，初始权重 0.5），
            让你不必先去手动建主题再点反馈。自动主题被反复否定会自动停用，手动创建的主题永远不会被自动停用。
            全过程透明、可解释、可回滚——确认、编辑、停用或删除任意主题即可改变推荐结果。
          </>
        }
      />

      {/* L2a：AI 归纳建议箱（模型不直接改数据，全部待人工确认） */}
      <Card
        className={styles['induction-card']}
        title={
          <Space size={8}>
            <RobotOutlined />
            <span>AI 归纳建议</span>
            {pendingCount > 0 && <Badge count={pendingCount} />}
          </Space>
        }
        extra={
          <Space>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {statusText}
            </Typography.Text>
            <Tooltip title="让模型回顾你最近的反馈，产出「新增 / 调权重 / 合并 / 停用 / 画像结论」建议。模型不会直接改你的关注主题，全部等你逐条确认。">
              <Button
                type="primary"
                ghost
                icon={<ThunderboltOutlined />}
                loading={inducting}
                onClick={handleInduct}
              >
                {inducting ? '正在归纳…' : '运行 AI 归纳'}
              </Button>
            </Tooltip>
          </Space>
        }
      >
        <Spin spinning={suggestionsLoading}>
          {pendingCount === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <span>
                  暂无待确认建议。这是正常的——模型只在积累到一定反馈量后才会归纳。
                  <br />
                  你可以点右上角「运行 AI 归纳」立即跑一次。
                </span>
              }
            />
          ) : (
            <List
              dataSource={suggestions}
              renderItem={(item) => {
                const meta = SUGGESTION_META[item.kind] || SUGGESTION_META.insight
                const busy = actingId === item.id
                return (
                  <List.Item
                    key={item.id}
                    actions={[
                      <Button
                        key="accept"
                        type="primary"
                        size="small"
                        icon={<CheckOutlined />}
                        loading={busy}
                        onClick={() => handleAccept(item)}
                      >
                        {meta.accept}
                      </Button>,
                      <Button
                        key="reject"
                        size="small"
                        icon={<CloseOutlined />}
                        disabled={busy}
                        onClick={() => handleReject(item)}
                      >
                        忽略
                      </Button>,
                    ]}
                  >
                    <List.Item.Meta
                      title={
                        <Space size={8} wrap>
                          <Tag color={meta.color}>{meta.label}</Tag>
                          <span className={styles['suggestion-name']}>{item.name}</span>
                          <Tooltip title={`模型自评置信度 ${(item.confidence * 100).toFixed(0)}%`}>
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              置信度 {(item.confidence * 100).toFixed(0)}%
                            </Typography.Text>
                          </Tooltip>
                          {item.evidence?.length > 0 && (
                            <Tooltip title="支撑这条建议的反馈条数（来自你点过的感兴趣 / 不感兴趣）">
                              <Tag>{item.evidence.length} 条反馈支撑</Tag>
                            </Tooltip>
                          )}
                        </Space>
                      }
                      description={
                        <div className={styles['suggestion-body']}>
                          <div>{describeSuggestion(item)}</div>
                          {item.reason && (
                            <div className={styles['suggestion-reason']}>理由：{item.reason}</div>
                          )}
                        </div>
                      }
                    />
                  </List.Item>
                )
              }}
            />
          )}
        </Spin>
      </Card>

      <Card>
        <div className={styles.toolbar}>
          <Segmented
            value={sourceFilter}
            onChange={(value) => setSourceFilter(value as SourceFilter)}
            options={[
              { label: `全部 (${topics.length})`, value: 'all' },
              { label: `手动创建 (${topics.length - autoCount})`, value: 'manual' },
              { label: `自动派生 (${autoCount})`, value: 'auto' },
            ]}
          />
        </div>
        <Table<FocusTopic>
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={visibleTopics}
          pagination={false}
          locale={{
            emptyText: (
              <Empty
                description={
                  sourceFilter === 'auto'
                    ? '还没有自动派生的主题。去资讯页对内容点「感兴趣」，系统会自动帮你长出关注主题'
                    : '还没有关注主题，点击右上角「新增关注」开始吧'
                }
              />
            ),
          }}
        />
      </Card>

      {/* 归纳历史：每次归纳留一张快照，可一键撤销该次归纳的影响 */}
      <Drawer
        title="归纳历史"
        width={520}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        extra={
          <Button size="small" icon={<ReloadOutlined />} onClick={openHistory}>
            刷新
          </Button>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="回滚是精确回退"
          description="回滚只会撤销「该次归纳」带来的改动（删除其新增的主题、还原它改过的权重与状态），你在归纳之后手动新建或修改的主题不受影响。"
        />
        <Spin spinning={revisionsLoading}>
          {revisions.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有归纳记录" />
          ) : (
            <List
              dataSource={revisions}
              renderItem={(rev) => (
                <div className={styles['revision-item']}>
                  <div className={styles['revision-head']}>
                    <Space size={6} wrap>
                      <Tag color={rev.current ? 'blue' : 'default'}>
                        {rev.current ? '当前基线' : rev.trigger === 'manual' ? '手动' : '自动'}
                      </Tag>
                      <Tag color={rev.status === 'rolled_back' ? 'default' : 'green'}>
                        {rev.status === 'rolled_back' ? '已回滚' : '已应用'}
                      </Tag>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {rev.created_at ? new Date(rev.created_at).toLocaleString() : ''}
                      </Typography.Text>
                    </Space>
                  </div>
                  <div className={styles['revision-meta']}>
                    样本 {rev.sample_count} 条反馈 · 主题 {rev.topic_count} 个 · 产出{' '}
                    {rev.diff?.length ?? 0} 条建议
                  </div>
                  {rev.summary && (
                    <div className={styles['revision-summary']}>结论：{rev.summary}</div>
                  )}
                  {rev.error && (
                    <div className={styles['revision-error']}>未产出：{rev.error}</div>
                  )}
                  {rev.diff?.length > 0 && (
                    <div className={styles['revision-diff']}>
                      {rev.diff.map((d, i) => (
                        <Tag key={i} style={{ marginBottom: 4 }}>
                          {revisionKindLabel(d.kind)}：{d.name}
                        </Tag>
                      ))}
                    </div>
                  )}
                  {rev.status !== 'rolled_back' && (
                    <>
                      <Divider style={{ margin: '8px 0' }} />
                      <Popconfirm
                        title="确认回滚这次归纳？"
                        description="将删除该次归纳新增的主题，并还原它修改过的权重与状态。"
                        okText="回滚"
                        cancelText="取消"
                        okButtonProps={{ danger: true }}
                        onConfirm={() => handleRollback(rev)}
                      >
                        <Button size="small" danger>
                          回滚到这次归纳之前
                        </Button>
                      </Popconfirm>
                    </>
                  )}
                </div>
              )}
            />
          )}
        </Spin>
      </Drawer>

      <Modal
        title={editing ? '编辑关注主题' : '新增关注主题'}
        open={modalOpen}
        onOk={handleSubmit}
        confirmLoading={saving}
        onCancel={() => setModalOpen(false)}
        okText="保存"
        cancelText="取消"
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="topic_type"
            label="主题类型"
            rules={[{ required: true, message: '请选择主题类型' }]}
          >
            <Select options={TOPIC_TYPE_OPTIONS} disabled={!!editing} />
          </Form.Item>
          <Form.Item
            name="name"
            label="主题名称"
            rules={[{ required: true, message: '请输入主题名称' }]}
            extra={editing ? '名称创建后不可修改，如需变更请删除后重建' : undefined}
          >
            <Input placeholder="例如：MDI、HPPO、某客户、废塑料化学回收" disabled={!!editing} />
          </Form.Item>
          <Form.Item
            name="query_text"
            label="匹配词"
            rules={[{ required: true, message: '请输入匹配词' }]}
            extra="用于匹配资讯标题/正文的关键词，可填写多个词（以空格分隔）。系统内置化工同义词表，写 PA6 也能匹配到「尼龙6 / 聚酰胺6」"
          >
            <Input.TextArea rows={2} placeholder="例如：MDI 价格 装置" />
          </Form.Item>
          <Form.Item
            name="weight"
            label="初始权重"
            extra="范围 -2 ~ 2，默认 1。权重越高，相关资讯排序越靠前（后续会随反馈自动调整）"
          >
            <InputNumber min={-2} max={2} step={0.1} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
