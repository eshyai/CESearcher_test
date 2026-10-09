import { useCallback, useEffect, useState } from 'react'
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
} from 'antd'
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  StarOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import * as api from '@/api'
import type { FocusTopic } from '@/api/news'
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

export default function TopicsPage() {
  const [topics, setTopics] = useState<FocusTopic[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<FocusTopic | null>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

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

  useEffect(() => {
    fetchTopics()
  }, [fetchTopics])

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

  const columns: ColumnsType<FocusTopic> = [
    {
      title: '关注主题',
      dataIndex: 'name',
      key: 'name',
      render: (_, record) => (
        <div className={styles['topic-cell']}>
          <div className={styles['topic-name']}>{record.name}</div>
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
      title: '关注权重',
      dataIndex: 'weight',
      key: 'weight',
      width: 220,
      render: (weight: number) => (
        <div className={styles['weight-cell']}>
          {/* 权重范围 [-2, 2]，归一化到 [0, 100%] 展示 */}
          <Progress
            percent={Number((((weight + 2) / 4) * 100).toFixed(0))}
            size="small"
            strokeColor={weight >= 1 ? '#cf1322' : weight >= 0 ? '#1890ff' : '#8c8c8c'}
            format={() => weight.toFixed(2)}
          />
        </div>
      ),
    },
    {
      title: '反馈',
      key: 'feedback',
      width: 140,
      render: (_, record) => (
        <Space size={4}>
          <Tag color="red">感兴趣 {record.positive_count}</Tag>
          <Tag color="default">不感兴趣 {record.negative_count}</Tag>
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
      width: 200,
      render: (_, record) => (
        <Space size={4}>
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

  return (
    <div className={styles['topics-page']}>
      <div className={styles.header}>
        <div className={styles['header-left']}>
          <StarOutlined style={{ fontSize: 24, marginRight: 12 }} />
          <h2>我的关注</h2>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新增关注
        </Button>
      </div>

      <Alert
        className={styles.tip}
        type="info"
        showIcon
        message="AI 如何自我成长"
        description="你在资讯页点击「感兴趣 / 不感兴趣」后，系统按有界规则更新相关主题权重：新权重 = clamp(0.85 × 旧权重 + 0.35 × 反馈, -2, 2)。权重越高的主题，相关资讯在资讯流中排序越靠前。整个过程透明、可解释、可回滚——删除主题或调整权重即可改变推荐结果。"
      />

      <Card>
        <Table<FocusTopic>
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={topics}
          pagination={false}
          locale={{ emptyText: '还没有关注主题，点击右上角「新增关注」开始吧' }}
        />
      </Card>

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
            extra="用于匹配资讯标题/正文的关键词，可填写多个词（以空格分隔）"
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
