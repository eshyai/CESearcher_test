import { useEffect, useState, useRef } from 'react'
import {
  Card,
  Button,
  List,
  Modal,
  Form,
  Input,
  Upload,
  message,
  Popconfirm,
  Tag,
  Empty,
  Spin,
  Progress,
  Typography,
  Drawer,
  Alert,
  Select,
  Space,
  Avatar,
  Tooltip,
  Radio,
  Divider,
} from 'antd'
import {
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  UploadOutlined,
  FileOutlined,
  FolderOutlined,
  ArrowLeftOutlined,
  ReloadOutlined,
  EyeOutlined,
  TeamOutlined,
  HistoryOutlined,
  UserOutlined,
  CrownOutlined,
  GlobalOutlined,
  LockOutlined,
} from '@ant-design/icons'
import { useSnapshot } from 'valtio'
import { knowledgeState, knowledgeActions, KnowledgeBase, KBDocument } from '@/store/knowledge'
import { authState } from '@/store/auth'
import { useNavigate } from 'react-router-dom'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/zh-cn'
import UploadModal, { UploadResult } from '@/components/upload-modal'
import ChunksDrawer from '@/components/chunks-drawer'
import * as knowledgeApi from '@/api/knowledge'
import type { KnowledgeBaseMember, KnowledgeAuditLog } from '@/api/knowledge'
import styles from './index.module.scss'

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

const { Text, Paragraph } = Typography

const STATUS_MAP: Record<string, { color: string; text: string }> = {
  pending: { color: 'default', text: '待处理' },
  processing: { color: 'processing', text: '处理中' },
  completed: { color: 'success', text: '已完成' },
  failed: { color: 'error', text: '处理失败' },
}

// 角色语义（与后端 ROLE_RANK 对齐）
const ROLE_META: Record<string, { color: string; text: string; desc: string }> = {
  viewer: { color: 'default', text: '只读', desc: '可查看知识库与文档' },
  editor: { color: 'blue', text: '可编辑', desc: '可上传 / 删除文档' },
  manager: { color: 'gold', text: '管理员', desc: '可管理成员、可见性与删除知识库' },
}

// 审计动作的中文展示
const ACTION_META: Record<string, { text: string; color: string }> = {
  create: { text: '创建知识库', color: 'green' },
  update: { text: '更新设置', color: 'blue' },
  soft_delete: { text: '删除知识库', color: 'red' },
  restore: { text: '恢复知识库', color: 'cyan' },
  document_upload: { text: '上传文档', color: 'geekblue' },
  document_delete: { text: '删除文档', color: 'volcano' },
  document_restore: { text: '恢复文档', color: 'cyan' },
  member_upsert: { text: '设置成员', color: 'purple' },
  member_remove: { text: '移除成员', color: 'magenta' },
}

const VISIBILITY_META: Record<string, { color: string; text: string; icon: React.ReactNode }> = {
  private: { color: 'default', text: '私有', icon: <LockOutlined /> },
  team: { color: 'cyan', text: '部门可见', icon: <GlobalOutlined /> },
}

export default function KnowledgePage() {
  const navigate = useNavigate()
  const { knowledgeBases, currentKnowledgeBase, loading, uploading } = useSnapshot(knowledgeState)
  const { isLoggedIn } = useSnapshot(authState)
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [editModalOpen, setEditModalOpen] = useState(false)
  const [editingKb, setEditingKb] = useState<KnowledgeBase | null>(null)
  const [form] = Form.useForm()
  const [editForm] = Form.useForm()
  const pollingRef = useRef<NodeJS.Timeout | null>(null)

  // 上传模态框状态
  const [uploadModalOpen, setUploadModalOpen] = useState(false)
  const [uploadingFile, setUploadingFile] = useState('')
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null)

  // 切片查看抽屉状态
  const [chunksDrawerOpen, setChunksDrawerOpen] = useState(false)
  const [selectedDoc, setSelectedDoc] = useState<{ id: string; filename: string } | null>(null)

  // 成员管理
  const [memberModalOpen, setMemberModalOpen] = useState(false)
  const [members, setMembers] = useState<KnowledgeBaseMember[]>([])
  const [membersLoading, setMembersLoading] = useState(false)
  const [memberSaving, setMemberSaving] = useState(false)
  const [newMemberName, setNewMemberName] = useState('')
  const [newMemberRole, setNewMemberRole] = useState<KnowledgeBaseMember['role']>('viewer')

  // 操作日志
  const [auditDrawerOpen, setAuditDrawerOpen] = useState(false)
  const [auditLogs, setAuditLogs] = useState<KnowledgeAuditLog[]>([])
  const [auditLoading, setAuditLoading] = useState(false)

  useEffect(() => {
    if (isLoggedIn) {
      knowledgeActions.fetchKnowledgeBases()
    }
    return () => {
      knowledgeActions.clearCurrentKnowledgeBase()
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
      }
    }
  }, [isLoggedIn])

  // Polling for document status updates
  useEffect(() => {
    if (currentKnowledgeBase) {
      const hasProcessing = currentKnowledgeBase.documents.some(
        (doc) => doc.status === 'pending' || doc.status === 'processing'
      )
      if (hasProcessing) {
        pollingRef.current = setInterval(() => {
          knowledgeActions.refreshDocuments(currentKnowledgeBase.id)
        }, 3000)
      }
    }
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current)
        pollingRef.current = null
      }
    }
  }, [currentKnowledgeBase?.id, currentKnowledgeBase?.documents])

  if (!isLoggedIn) {
    return (
      <div className={styles['knowledge-page']}>
        <div className={styles['empty-state']}>
          <Empty description="请先登录" />
          <Button type="primary" onClick={() => navigate('/login')}>
            去登录
          </Button>
        </div>
      </div>
    )
  }

  const handleCreateKb = async (values: {
    name: string
    description?: string
    visibility: 'private' | 'team'
  }) => {
    try {
      await knowledgeActions.createKnowledgeBase(
        values.name,
        values.description,
        values.visibility || 'private'
      )
      message.success('知识库创建成功')
      setCreateModalOpen(false)
      form.resetFields()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '创建失败')
    }
  }

  const handleEditKb = async (values: {
    name: string
    description?: string
    visibility: 'private' | 'team'
  }) => {
    if (!editingKb) return
    try {
      await knowledgeActions.updateKnowledgeBase(
        editingKb.id,
        values.name,
        values.description,
        values.visibility
      )
      message.success('知识库更新成功')
      setEditModalOpen(false)
      setEditingKb(null)
      editForm.resetFields()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '更新失败')
    }
  }

  const handleDeleteKb = async (kbId: string) => {
    try {
      await knowledgeActions.deleteKnowledgeBase(kbId)
      message.success('知识库删除成功')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  const handleKbClick = (kb: KnowledgeBase) => {
    knowledgeActions.fetchKnowledgeBase(kb.id)
  }

  const handleBackToList = () => {
    knowledgeActions.clearCurrentKnowledgeBase()
  }

  const handleUpload = async (file: File) => {
    if (!currentKnowledgeBase) return false

    // 打开上传模态框
    setUploadModalOpen(true)
    setUploadingFile(file.name)
    setUploadResult(null)

    try {
      const result = await knowledgeActions.uploadDocument(currentKnowledgeBase.id, file)
      setUploadResult({
        success: true,
        message: '上传成功',
        filename: file.name,
        docId: result?.id,
      })
    } catch (error: any) {
      setUploadResult({
        success: false,
        message: '上传失败',
        filename: file.name,
        error: error?.response?.data?.detail || error?.message || '网络请求失败',
      })
    }
    return false
  }

  // 关闭上传模态框
  const handleCloseUploadModal = () => {
    if (!uploading) {
      setUploadModalOpen(false)
      setUploadResult(null)
      setUploadingFile('')
    }
  }

  // 查看切片
  const handleViewChunks = (doc: KBDocument) => {
    if (doc.status !== 'completed') {
      message.warning('文档尚未处理完成')
      return
    }
    setSelectedDoc({ id: doc.id, filename: doc.filename })
    setChunksDrawerOpen(true)
  }

  // 关闭切片抽屉
  const handleCloseChunksDrawer = () => {
    setChunksDrawerOpen(false)
    setSelectedDoc(null)
  }

  const handleDeleteDocument = async (docId: string) => {
    if (!currentKnowledgeBase) return
    try {
      await knowledgeActions.deleteDocument(currentKnowledgeBase.id, docId)
      message.success('文档删除成功')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  const openEditModal = (kb: KnowledgeBase, e: React.MouseEvent) => {
    e.stopPropagation()
    setEditingKb(kb)
    editForm.setFieldsValue({
      name: kb.name,
      description: kb.description,
      visibility: kb.visibility || 'private',
    })
    setEditModalOpen(true)
  }

  // ---------- 成员管理 ----------
  const loadMembers = async (kbId: string) => {
    setMembersLoading(true)
    try {
      const res = await knowledgeApi.getKnowledgeBaseMembers(kbId)
      setMembers(res.data)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取成员失败')
    } finally {
      setMembersLoading(false)
    }
  }

  const openMemberModal = () => {
    if (!currentKnowledgeBase) return
    setMemberModalOpen(true)
    setNewMemberName('')
    setNewMemberRole('viewer')
    loadMembers(currentKnowledgeBase.id)
  }

  const handleAddMember = async () => {
    if (!currentKnowledgeBase) return
    const username = newMemberName.trim()
    if (!username) {
      message.warning('请输入要添加的用户名')
      return
    }
    setMemberSaving(true)
    try {
      await knowledgeApi.setKnowledgeBaseMember(currentKnowledgeBase.id, {
        username,
        role: newMemberRole,
      })
      message.success(`已添加成员 ${username}`)
      setNewMemberName('')
      setNewMemberRole('viewer')
      await loadMembers(currentKnowledgeBase.id)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '添加成员失败')
    } finally {
      setMemberSaving(false)
    }
  }

  const handleRemoveMember = async (m: KnowledgeBaseMember) => {
    if (!currentKnowledgeBase) return
    try {
      await knowledgeApi.removeKnowledgeBaseMember(currentKnowledgeBase.id, m.user_id)
      message.success(`已移除 ${m.username}`)
      await loadMembers(currentKnowledgeBase.id)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '移除失败')
    }
  }

  // ---------- 操作日志 ----------
  const openAuditDrawer = async () => {
    if (!currentKnowledgeBase) return
    setAuditDrawerOpen(true)
    setAuditLoading(true)
    try {
      const res = await knowledgeApi.getKnowledgeBaseAudit(currentKnowledgeBase.id)
      setAuditLogs(res.data)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取操作日志失败')
    } finally {
      setAuditLoading(false)
    }
  }

  const renderAuditDetail = (log: KnowledgeAuditLog) => {
    const d = log.detail || {}
    const parts: string[] = []
    if (d.username) parts.push(`成员 ${d.username}`)
    if (d.role) parts.push(`角色 ${ROLE_META[d.role]?.text || d.role}`)
    if (d.visibility) parts.push(`可见性 ${VISIBILITY_META[d.visibility]?.text || d.visibility}`)
    if (d.filename) parts.push(`文件 ${d.filename}`)
    if (typeof d.document_id === 'string') parts.push(`文档 ${d.document_id.slice(0, 8)}`)
    if (typeof d.user_id === 'string') parts.push(`用户 ${d.user_id.slice(0, 8)}`)
    const keys = Object.keys(d)
    if (!parts.length && keys.length) parts.push(JSON.stringify(d))
    return parts.join(' · ')
  }

  const renderKbList = () => (
    <div className={styles['kb-list']}>
      <div className={styles['header']}>
        <h2>知识库</h2>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModalOpen(true)}>
          创建知识库
        </Button>
      </div>

      <Spin spinning={loading}>
        {knowledgeBases.length === 0 ? (
          <div className={styles['empty-state']}>
            <Empty description="暂无知识库" />
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateModalOpen(true)}>
              创建第一个知识库
            </Button>
          </div>
        ) : (
          <div className={styles['card-grid']}>
            {(knowledgeBases as KnowledgeBase[]).map((kb) => {
              const roleMeta = ROLE_META[kb.role] || ROLE_META.viewer
              const visMeta = VISIBILITY_META[kb.visibility] || VISIBILITY_META.private
              const canManage = kb.role === 'manager'
              return (
                <Card
                  key={kb.id}
                  className={styles['kb-card']}
                  hoverable
                  onClick={() => handleKbClick(kb)}
                >
                  <div className={styles['card-icon']}>
                    <FolderOutlined />
                  </div>
                  <div className={styles['card-content']}>
                    <Text strong ellipsis={{ tooltip: kb.name }} className={styles['card-title']}>
                      {kb.name}
                    </Text>
                    <Paragraph
                      type="secondary"
                      ellipsis={{ rows: 2 }}
                      className={styles['card-desc']}
                    >
                      {kb.description || '暂无描述'}
                    </Paragraph>
                    <div className={styles['card-badges']}>
                      <Tooltip title={kb.visibility === 'team' ? '部门内所有成员可查看' : '仅创建者与成员可见'}>
                        <Tag icon={visMeta.icon} color={visMeta.color}>
                          {visMeta.text}
                        </Tag>
                      </Tooltip>
                      <Tooltip title={roleMeta.desc}>
                        <Tag color={roleMeta.color}>我的角色：{roleMeta.text}</Tag>
                      </Tooltip>
                    </div>
                    <div className={styles['card-meta']}>
                      <span>{kb.document_count} 个文档</span>
                      <span>{dayjs(kb.updated_at).fromNow()}</span>
                    </div>
                  </div>
                  {canManage && (
                    <div className={styles['card-actions']}>
                      <Button
                        type="text"
                        size="small"
                        icon={<EditOutlined />}
                        onClick={(e) => openEditModal(kb, e)}
                      />
                      <Popconfirm
                        title="确定删除此知识库？"
                        description="删除后将无法恢复"
                        onConfirm={(e) => {
                          e?.stopPropagation()
                          handleDeleteKb(kb.id)
                        }}
                        onCancel={(e) => e?.stopPropagation()}
                      >
                        <Button
                          type="text"
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                          onClick={(e) => e.stopPropagation()}
                        />
                      </Popconfirm>
                    </div>
                  )}
                </Card>
              )
            })}
          </div>
        )}
      </Spin>
    </div>
  )

  const renderKbDetail = () => {
    if (!currentKnowledgeBase) return null
    const role = currentKnowledgeBase.role || 'manager'
    const canManage = role === 'manager'
    const canEdit = role === 'editor' || role === 'manager'
    const visMeta = VISIBILITY_META[currentKnowledgeBase.visibility] || VISIBILITY_META.private

    return (
      <div className={styles['kb-detail']}>
        <div className={styles['header']}>
          <div className={styles['header-left']}>
            <Button type="text" icon={<ArrowLeftOutlined />} onClick={handleBackToList}>
              返回
            </Button>
            <h2>{currentKnowledgeBase.name}</h2>
            <Tag icon={visMeta.icon} color={visMeta.color}>
              {visMeta.text}
            </Tag>
            <Text type="secondary">({currentKnowledgeBase.document_count} 个文档)</Text>
            <Tooltip title="每次设置/文档变更都会 +1，用于协作时判断是否被改动">
              <Text type="secondary" className={styles['revision']}>
                rev.{currentKnowledgeBase.revision}
              </Text>
            </Tooltip>
          </div>
          <div className={styles['header-right']}>
            <Button icon={<TeamOutlined />} onClick={openMemberModal}>
              成员管理
            </Button>
            {canManage && (
              <Button icon={<HistoryOutlined />} onClick={openAuditDrawer}>
                操作日志
              </Button>
            )}
            <Button
              icon={<ReloadOutlined />}
              onClick={() => knowledgeActions.refreshDocuments(currentKnowledgeBase.id)}
            >
              刷新
            </Button>
            {canEdit && (
              <Upload
                beforeUpload={handleUpload}
                showUploadList={false}
                accept=".pdf,.doc,.docx,.txt,.md,.html,.xlsx,.xls,.pptx,.ppt,.jpg,.jpeg,.png,.gif,.webp,.bmp,.py,.js,.ts,.json,.yaml,.yml,.xml,.csv"
              >
                <Button type="primary" icon={<UploadOutlined />} loading={uploading}>
                  上传文档
                </Button>
              </Upload>
            )}
          </div>
        </div>

        {currentKnowledgeBase.description && (
          <Paragraph type="secondary" className={styles['description']}>
            {currentKnowledgeBase.description}
          </Paragraph>
        )}

        {role === 'viewer' && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message="你在该知识库中是只读角色，可以浏览文档与切片，但不能上传或删除。如需编辑请联系管理员提权。"
          />
        )}

        <Spin spinning={loading}>
          {currentKnowledgeBase.documents.length === 0 ? (
            <div className={styles['empty-state']}>
              <Empty description="暂无文档" />
              {canEdit && (
                <Upload
                  beforeUpload={handleUpload}
                  showUploadList={false}
                  accept=".pdf,.doc,.docx,.txt,.md,.html,.xlsx,.xls,.pptx,.ppt,.jpg,.jpeg,.png,.gif,.webp,.bmp,.py,.js,.ts,.json,.yaml,.yml,.xml,.csv"
                >
                  <Button type="primary" icon={<UploadOutlined />}>
                    上传第一个文档
                  </Button>
                </Upload>
              )}
            </div>
          ) : (
            <List
              className={styles['doc-list']}
              dataSource={currentKnowledgeBase.documents as KBDocument[]}
              renderItem={(doc) => (
                <List.Item
                  className={styles['doc-item']}
                  actions={[
                    <Button
                      key="view"
                      type="text"
                      size="small"
                      icon={<EyeOutlined />}
                      onClick={() => handleViewChunks(doc)}
                      disabled={doc.status !== 'completed'}
                      title="查看切片"
                    />,
                    canEdit ? (
                      <Popconfirm
                        key="delete"
                        title="确定删除此文档？"
                        onConfirm={() => handleDeleteDocument(doc.id)}
                      >
                        <Button type="text" danger size="small" icon={<DeleteOutlined />} />
                      </Popconfirm>
                    ) : null,
                  ].filter(Boolean)}
                >
                  <List.Item.Meta
                    avatar={<FileOutlined style={{ fontSize: 24, color: '#1890ff' }} />}
                    title={
                      <div className={styles['doc-title']}>
                        <Text ellipsis={{ tooltip: doc.filename }}>{doc.filename}</Text>
                        <Tag color={STATUS_MAP[doc.status]?.color}>
                          {STATUS_MAP[doc.status]?.text}
                        </Tag>
                      </div>
                    }
                    description={
                      <div className={styles['doc-meta']}>
                        <span>
                          {doc.file_type?.toUpperCase()} · {formatFileSize(doc.file_size)}
                        </span>
                        {doc.status === 'completed' && (
                          <span
                            className={styles['chunk-link']}
                            onClick={() => handleViewChunks(doc)}
                          >
                            {doc.chunk_count} 个切片
                          </span>
                        )}
                        {doc.status === 'processing' && (
                          <Progress percent={30} size="small" style={{ width: 100 }} />
                        )}
                        {doc.status === 'failed' && doc.error_message && (
                          <Text type="danger" style={{ fontSize: 12 }}>
                            {doc.error_message}
                          </Text>
                        )}
                        <span>{dayjs(doc.created_at).fromNow()}</span>
                      </div>
                    }
                  />
                </List.Item>
              )}
            />
          )}
        </Spin>
      </div>
    )
  }

  return (
    <div className={styles['knowledge-page']}>
      {currentKnowledgeBase ? renderKbDetail() : renderKbList()}

      <Modal
        title="创建知识库"
        open={createModalOpen}
        onCancel={() => {
          setCreateModalOpen(false)
          form.resetFields()
        }}
        footer={null}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleCreateKb}
          autoComplete="off"
          initialValues={{ visibility: 'private' }}
        >
          <Form.Item
            name="name"
            label="知识库名称"
            rules={[{ required: true, message: '请输入知识库名称' }]}
          >
            <Input placeholder="请输入知识库名称" maxLength={255} autoComplete="off" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea placeholder="请输入知识库描述（可选）" rows={3} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="visibility"
            label="可见性"
            extra="私有：仅你与显式添加的成员可见；部门可见：部门内所有登录用户可只读查看"
          >
            <Radio.Group>
              <Radio.Button value="private">
                <LockOutlined /> 私有
              </Radio.Button>
              <Radio.Button value="team">
                <GlobalOutlined /> 部门可见
              </Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Button onClick={() => setCreateModalOpen(false)} style={{ marginRight: 8 }}>
              取消
            </Button>
            <Button type="primary" htmlType="submit">
              创建
            </Button>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑知识库"
        open={editModalOpen}
        onCancel={() => {
          setEditModalOpen(false)
          setEditingKb(null)
          editForm.resetFields()
        }}
        footer={null}
      >
        <Form form={editForm} layout="vertical" onFinish={handleEditKb} autoComplete="off">
          <Form.Item
            name="name"
            label="知识库名称"
            rules={[{ required: true, message: '请输入知识库名称' }]}
          >
            <Input placeholder="请输入知识库名称" maxLength={255} autoComplete="off" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea placeholder="请输入知识库描述（可选）" rows={3} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="visibility"
            label="可见性"
            extra="切换到「部门可见」后，部门内所有登录用户会以只读身份看到该知识库"
          >
            <Radio.Group>
              <Radio.Button value="private">
                <LockOutlined /> 私有
              </Radio.Button>
              <Radio.Button value="team">
                <GlobalOutlined /> 部门可见
              </Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Button
              onClick={() => {
                setEditModalOpen(false)
                setEditingKb(null)
                editForm.resetFields()
              }}
              style={{ marginRight: 8 }}
            >
              取消
            </Button>
            <Button type="primary" htmlType="submit">
              保存
            </Button>
          </Form.Item>
        </Form>
      </Modal>

      {/* 成员管理 */}
      <Modal
        title={
          <Space>
            <TeamOutlined />
            成员管理
          </Space>
        }
        open={memberModalOpen}
        onCancel={() => setMemberModalOpen(false)}
        footer={null}
        width={560}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="角色说明"
          description={
            <div>
              <div>
                <b>只读</b>：查看知识库与文档 · <b>可编辑</b>：额外可上传/删除文档 ·{' '}
                <b>管理员</b>：额外可管理成员、可见性
              </div>
              <div style={{ marginTop: 4, opacity: 0.75 }}>
                创建者固定为管理员，不可移除或降级。
              </div>
            </div>
          }
        />

        {currentKnowledgeBase?.role === 'manager' && (
          <>
            <Space.Compact style={{ width: '100%', marginBottom: 12 }}>
              <Input
                placeholder="输入用户名（如 test113）"
                prefix={<UserOutlined />}
                value={newMemberName}
                onChange={(e) => setNewMemberName(e.target.value)}
                onPressEnter={handleAddMember}
                allowClear
              />
              <Select
                value={newMemberRole}
                style={{ width: 120 }}
                onChange={(v) => setNewMemberRole(v)}
                options={[
                  { label: '只读', value: 'viewer' },
                  { label: '可编辑', value: 'editor' },
                  { label: '管理员', value: 'manager' },
                ]}
              />
              <Button type="primary" loading={memberSaving} onClick={handleAddMember}>
                添加
              </Button>
            </Space.Compact>
            <Divider style={{ margin: '8px 0 12px' }} />
          </>
        )}

        <Spin spinning={membersLoading}>
          <List
            dataSource={members}
            locale={{ emptyText: '暂无成员' }}
            renderItem={(m) => {
              const roleMeta = ROLE_META[m.role] || ROLE_META.viewer
              return (
                <List.Item
                  actions={
                    currentKnowledgeBase?.role === 'manager' && !m.is_owner
                      ? [
                          <Popconfirm
                            key="rm"
                            title={`移除成员 ${m.username}？`}
                            onConfirm={() => handleRemoveMember(m)}
                          >
                            <Button type="text" danger size="small" icon={<DeleteOutlined />} />
                          </Popconfirm>,
                        ]
                      : []
                  }
                >
                  <List.Item.Meta
                    avatar={<Avatar icon={<UserOutlined />} />}
                    title={
                      <Space size={6}>
                        <Text strong>{m.username}</Text>
                        {m.is_owner && (
                          <Tag icon={<CrownOutlined />} color="gold">
                            创建者
                          </Tag>
                        )}
                      </Space>
                    }
                    description={
                      <Space size={6}>
                        <Tag color={roleMeta.color}>{roleMeta.text}</Tag>
                        {m.created_at && (
                          <Text type="secondary" style={{ fontSize: 12 }}>
                            {dayjs(m.created_at).format('YYYY-MM-DD HH:mm')} 加入
                          </Text>
                        )}
                      </Space>
                    }
                  />
                </List.Item>
              )
            }}
          />
        </Spin>
      </Modal>

      {/* 操作日志 */}
      <Drawer
        title={
          <Space>
            <HistoryOutlined />
            操作日志
          </Space>
        }
        placement="right"
        width={420}
        open={auditDrawerOpen}
        onClose={() => setAuditDrawerOpen(false)}
      >
        <Spin spinning={auditLoading}>
          {auditLogs.length === 0 && !auditLoading ? (
            <Empty description="暂无记录" />
          ) : (
            <List
              dataSource={auditLogs}
              renderItem={(log) => {
                const meta = ACTION_META[log.action] || { text: log.action, color: 'default' }
                const detail = renderAuditDetail(log)
                return (
                  <List.Item className={styles['audit-item']}>
                    <div className={styles['audit-row']}>
                      <Tag color={meta.color}>{meta.text}</Tag>
                      <span className={styles['audit-time']}>
                        {dayjs(log.created_at).format('YYYY-MM-DD HH:mm:ss')}
                      </span>
                    </div>
                    {detail && <div className={styles['audit-detail']}>{detail}</div>}
                  </List.Item>
                )
              }}
            />
          )}
        </Spin>
      </Drawer>

      {/* 上传进度模态框 */}
      <UploadModal
        open={uploadModalOpen}
        uploading={uploading}
        result={uploadResult}
        filename={uploadingFile}
        onClose={handleCloseUploadModal}
      />

      {/* 切片查看抽屉 */}
      <ChunksDrawer
        open={chunksDrawerOpen}
        kbId={currentKnowledgeBase?.id || ''}
        docId={selectedDoc?.id || ''}
        filename={selectedDoc?.filename || ''}
        onClose={handleCloseChunksDrawer}
      />
    </div>
  )
}

function formatFileSize(bytes?: number): string {
  if (!bytes) return '未知'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
