import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Empty,
  Input,
  Popconfirm,
  Skeleton,
  Tabs,
  Tag,
  message,
} from 'antd'
import {
  ClockCircleOutlined,
  LinkOutlined,
  MessageOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useSearchParams } from 'react-router-dom'
import * as api from '@/api'
import type { LibraryNewsDetail, LibraryNewsItem } from '@/api/news'
import styles from './index.module.scss'

type LibStatus = 'active' | 'archived'

function formatDate(value?: string | null) {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '-'
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

function formatDateTime(value?: string | null) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function LibraryPage() {
  const [searchParams] = useSearchParams()

  const [status, setStatus] = useState<LibStatus>('active')
  const [keyword, setKeyword] = useState('')
  const [list, setList] = useState<LibraryNewsItem[]>([])
  const [total, setTotal] = useState(0)
  const [listLoading, setListLoading] = useState(false)

  const [selectedId, setSelectedId] = useState<string | null>(searchParams.get('news'))
  const [detail, setDetail] = useState<LibraryNewsDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const [composer, setComposer] = useState('')
  const [posting, setPosting] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingText, setEditingText] = useState('')
  const [savingEdit, setSavingEdit] = useState<string | null>(null)
  const [statusBusy, setStatusBusy] = useState(false)

  const fetchList = useCallback(async () => {
    setListLoading(true)
    try {
      const res = await api.news.getLibraryItems({ status, keyword: keyword || undefined, limit: 50 })
      setList(res.data)
      setTotal(res.total)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '加载共享库失败')
      setList([])
      setTotal(0)
    } finally {
      setListLoading(false)
    }
  }, [status, keyword])

  useEffect(() => {
    fetchList()
  }, [fetchList])

  const fetchDetail = useCallback(async (newsId: string) => {
    setDetailLoading(true)
    try {
      const res = await api.news.getLibraryDetail(newsId)
      setDetail(res.data)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '加载详情失败')
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  useEffect(() => {
    if (selectedId) fetchDetail(selectedId)
    else setDetail(null)
  }, [selectedId, fetchDetail])

  // 列表加载完但没选中项时，默认打开第一条，避免右侧空白
  useEffect(() => {
    if (!selectedId && list.length > 0) setSelectedId(list[0].id)
  }, [list, selectedId])

  const handlePost = async () => {
    const text = composer.trim()
    if (!selectedId || !text) return
    setPosting(true)
    try {
      const res = await api.news.addNewsAnnotation(selectedId, text)
      setComposer('')
      setDetail((prev) =>
        prev
          ? {
              ...prev,
              annotations: [...prev.annotations, res.data],
              annotation_count: (prev.annotation_count || 0) + 1,
            }
          : prev,
      )
      setList((prev) =>
        prev.map((it) =>
          it.id === selectedId
            ? { ...it, annotation_count: (it.annotation_count || 0) + 1 }
            : it,
        ),
      )
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '发布批注失败')
    } finally {
      setPosting(false)
    }
  }

  const handleSaveEdit = async (annotationId: string) => {
    const text = editingText.trim()
    if (!text) return
    setSavingEdit(annotationId)
    try {
      const res = await api.news.updateNewsAnnotation(annotationId, text)
      setDetail((prev) =>
        prev
          ? {
              ...prev,
              annotations: prev.annotations.map((a) => (a.id === annotationId ? res.data : a)),
            }
          : prev,
      )
      setEditingId(null)
      setEditingText('')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '保存失败')
    } finally {
      setSavingEdit(null)
    }
  }

  const handleDelete = async (annotationId: string) => {
    try {
      await api.news.deleteNewsAnnotation(annotationId)
      setDetail((prev) =>
        prev
          ? {
              ...prev,
              annotations: prev.annotations.filter((a) => a.id !== annotationId),
              annotation_count: Math.max(0, (prev.annotation_count || 1) - 1),
            }
          : prev,
      )
      setList((prev) =>
        prev.map((it) =>
          it.id === selectedId
            ? { ...it, annotation_count: Math.max(0, (it.annotation_count || 1) - 1) }
            : it,
        ),
      )
      message.success('批注已删除')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  // 下架 / 恢复：两个方向都改变「谁能看到」，所以刷新列表而不是只改本地状态
  const handleArchive = async () => {
    if (!selectedId) return
    setStatusBusy(true)
    try {
      await api.news.archiveLibraryItem(selectedId)
      message.success('已下架，可在「已下架」里重新收录')
      await fetchList()
      await fetchDetail(selectedId)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '下架失败')
    } finally {
      setStatusBusy(false)
    }
  }

  const handleRestore = async () => {
    if (!selectedId) return
    setStatusBusy(true)
    try {
      await api.news.addToLibrary(selectedId)
      message.success('已重新收录到共享库')
      await fetchList()
      await fetchDetail(selectedId)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '收录失败')
    } finally {
      setStatusBusy(false)
    }
  }

  return (
    <div className={styles['library-page']}>
      <div className={styles.header}>
        <div>
          <h2>共享资讯库</h2>
          <p className={styles['header__desc']}>
            团队共同维护的资讯池：任何人都能收录，原文只读，观点写在批注里；下架是软删，可随时恢复。
          </p>
        </div>
        <Button icon={<ReloadOutlined />} onClick={fetchList} loading={listLoading}>
          刷新
        </Button>
      </div>

      <div className={styles.toolbar}>
        <Tabs
          activeKey={status}
          onChange={(key) => {
            setStatus(key as LibStatus)
            setSelectedId(null)
          }}
          items={[
            { key: 'active', label: '已收录' },
            { key: 'archived', label: '已下架' },
          ]}
        />
        <Input.Search
          allowClear
          placeholder="搜索标题 / 摘要 / 正文"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={fetchList}
          style={{ maxWidth: 320 }}
        />
        <span className={styles.toolbar__count}>共 {total} 条</span>
      </div>

      <div className={styles.body}>
        <div className={styles.list}>
          {listLoading ? (
            <Skeleton active paragraph={{ rows: 6 }} />
          ) : list.length === 0 ? (
            <div className={styles['list__empty']}>
              <Empty
                description={
                  status === 'active'
                    ? '共享库还是空的：去「行业资讯」把值得留的收录进来'
                    : '没有已下架的资讯'
                }
              />
            </div>
          ) : (
            list.map((item) => (
              <div
                key={item.id}
                className={
                  selectedId === item.id
                    ? `${styles['lib-item']} ${styles['lib-item--active']}`
                    : styles['lib-item']
                }
                onClick={() => setSelectedId(item.id)}
              >
                <div className={styles['lib-item__title']}>{item.title}</div>
                <div className={styles['lib-item__meta']}>
                  <span>{item.source || '未知来源'}</span>
                  <span>
                    <ClockCircleOutlined /> {formatDate(item.publish_time || item.collected_at)}
                  </span>
                  {(item.annotation_count || 0) > 0 && (
                    <span className={styles['lib-item__notes']}>
                      <MessageOutlined /> {item.annotation_count}
                    </span>
                  )}
                </div>
                <div className={styles['lib-item__by']}>
                  收录 · {item.added_by_name || '已注销用户'} · {formatDate(item.added_at)}
                </div>
              </div>
            ))
          )}
        </div>

        <div className={styles.detail}>
          {!selectedId ? (
            <div className={styles['detail__empty']}>
              <Empty description="从左侧选一条资讯" />
            </div>
          ) : detailLoading || !detail ? (
            <Skeleton active paragraph={{ rows: 8 }} />
          ) : (
            <>
              <div className={styles['detail__head']}>
                <h3>{detail.title}</h3>
                <div className={styles['detail__meta']}>
                  {detail.status === 'archived' ? (
                    <Tag>已下架</Tag>
                  ) : detail.status === 'active' ? (
                    <Tag color="green">在库</Tag>
                  ) : (
                    <Tag color="orange">未收录</Tag>
                  )}
                  <span>{detail.source || '未知来源'}</span>
                  <span>{formatDate(detail.publish_time || detail.collected_at)}</span>
                  {detail.added_by_name && (
                    <span>
                      收录 by {detail.added_by_name} · {formatDate(detail.added_at)}
                    </span>
                  )}
                  {detail.source_url && (
                    <a onClick={() => window.open(detail.source_url as string, '_blank')}>
                      <LinkOutlined /> 原文
                    </a>
                  )}
                </div>
                <div className={styles['detail__ops']}>
                  {detail.status === 'active' ? (
                    <Popconfirm
                      title="下架后所有人都看不到，可再收录恢复"
                      okText="下架"
                      cancelText="取消"
                      onConfirm={handleArchive}
                    >
                      <Button size="small" loading={statusBusy}>
                        下架
                      </Button>
                    </Popconfirm>
                  ) : (
                    <Button size="small" type="primary" loading={statusBusy} onClick={handleRestore}>
                      {detail.status === 'archived' ? '重新收录' : '收录到共享库'}
                    </Button>
                  )}
                </div>
              </div>

              <div className={styles['detail__body']}>
                {detail.summary || detail.content || '这条资讯没有正文，点右上角「原文」查看。'}
              </div>

              <div className={styles.notes}>
                <div className={styles['notes__title']}>
                  批注 <span>{detail.annotations.length}</span>
                </div>

                {detail.status === 'archived' && (
                  <div className={styles['notes__tip']}>已下架的资讯仍可批注，重新收录后一起恢复可见。</div>
                )}

                <div className={styles['notes__composer']}>
                  <Input.TextArea
                    rows={3}
                    maxLength={2000}
                    value={composer}
                    onChange={(e) => setComposer(e.target.value)}
                    placeholder="写下你的判断：这条为什么值得留、能用在哪儿（所有人可见）"
                  />
                  <div className={styles['notes__composer-bar']}>
                    <span className={styles['notes__hint']}>
                      批注对所有登录用户可见；只能改/删自己的
                    </span>
                    <Button
                      type="primary"
                      loading={posting}
                      disabled={!composer.trim()}
                      onClick={handlePost}
                    >
                      发布批注
                    </Button>
                  </div>
                </div>

                {detail.annotations.length === 0 ? (
                  <div className={styles['notes__empty']}>还没有批注，第一条就归你了</div>
                ) : (
                  detail.annotations.map((ann) => (
                    <div key={ann.id} className={styles.note}>
                      <div className={styles['note__head']}>
                        <span className={styles['note__author']}>
                          {ann.username || '已注销用户'}
                        </span>
                        <span className={styles['note__time']}>
                          {formatDateTime(ann.created_at)}
                          {ann.updated_at ? ' · 已编辑' : ''}
                        </span>
                        {ann.mine && editingId !== ann.id && (
                          <span className={styles['note__ops']}>
                            <a
                              onClick={() => {
                                setEditingId(ann.id)
                                setEditingText(ann.content)
                              }}
                            >
                              编辑
                            </a>
                            <Popconfirm
                              title="删除这条批注？"
                              okText="删除"
                              cancelText="取消"
                              onConfirm={() => handleDelete(ann.id)}
                            >
                              <a>删除</a>
                            </Popconfirm>
                          </span>
                        )}
                      </div>
                      {editingId === ann.id ? (
                        <div className={styles['note__editor']}>
                          <Input.TextArea
                            rows={2}
                            maxLength={2000}
                            value={editingText}
                            onChange={(e) => setEditingText(e.target.value)}
                          />
                          <div className={styles['note__editor-bar']}>
                            <Button
                              size="small"
                              onClick={() => {
                                setEditingId(null)
                                setEditingText('')
                              }}
                            >
                              取消
                            </Button>
                            <Button
                              size="small"
                              type="primary"
                              loading={savingEdit === ann.id}
                              disabled={!editingText.trim()}
                              onClick={() => handleSaveEdit(ann.id)}
                            >
                              保存
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <div className={styles['note__body']}>{ann.content}</div>
                      )}
                    </div>
                  ))
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
