import { request } from './request'

export interface KnowledgeBase {
  id: string
  name: string
  description?: string
  document_count: number
  is_shared: boolean
  revision: number
  role: 'viewer' | 'editor' | 'manager'
  created_at: string
  updated_at: string
}

export interface KBDocument {
  id: string
  knowledge_base_id: string
  filename: string
  file_type?: string
  file_size?: number
  status: 'pending' | 'processing' | 'completed' | 'failed'
  chunk_count: number
  error_message?: string
  revision: number
  deleted_at?: string | null
  created_at: string
  updated_at: string
}

export interface KnowledgeBaseWithDocuments extends KnowledgeBase {
  documents: KBDocument[]
}

export interface CreateKnowledgeBaseParams {
  name: string
  description?: string
  is_shared?: boolean
}

export interface UpdateKnowledgeBaseParams {
  name?: string
  description?: string
  is_shared?: boolean
}

/**
 * 获取知识库列表
 */
export function getKnowledgeBases() {
  return request.get<KnowledgeBase[]>('/knowledge-bases', { loading: false })
}

/**
 * 创建知识库
 */
export function createKnowledgeBase(params: CreateKnowledgeBaseParams) {
  return request.post<KnowledgeBase>('/knowledge-bases', params)
}

/**
 * 获取知识库详情（包含文档列表）
 */
export function getKnowledgeBase(kbId: string) {
  return request.get<KnowledgeBaseWithDocuments>(`/knowledge-bases/${kbId}`, { loading: false })
}

/**
 * 更新知识库
 */
export function updateKnowledgeBase(kbId: string, params: UpdateKnowledgeBaseParams) {
  return request.put<KnowledgeBase>(`/knowledge-bases/${kbId}`, params)
}

/**
 * 删除知识库
 */
export function deleteKnowledgeBase(kbId: string) {
  return request.delete(`/knowledge-bases/${kbId}`)
}

export interface KnowledgeBaseMember {
  user_id: string
  username: string
  role: 'viewer' | 'editor' | 'manager'
  created_at?: string
}

export function getKnowledgeBaseMembers(kbId: string) {
  return request.get<KnowledgeBaseMember[]>(`/knowledge-bases/${kbId}/members`, { loading: false })
}

export function setKnowledgeBaseMember(
  kbId: string,
  payload: { username: string; role: KnowledgeBaseMember['role'] },
) {
  return request.put<KnowledgeBaseMember>(`/knowledge-bases/${kbId}/members`, payload)
}

export function removeKnowledgeBaseMember(kbId: string, userId: string) {
  return request.delete(`/knowledge-bases/${kbId}/members/${userId}`)
}

export function restoreKnowledgeBase(kbId: string) {
  return request.post<KnowledgeBase>(`/knowledge-bases/${kbId}/restore`)
}

/**
 * 上传文档到知识库
 */
export function uploadDocument(kbId: string, file: File) {
  const formData = new FormData()
  formData.append('file', file)
  return request.post<{ status: string; id: string; filename: string; process_status: string; message: string }>(
    `/knowledge-bases/${kbId}/documents`,
    formData,
    {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      // 禁用全局 loading，使用 UploadModal 展示进度
      loading: false,
      // 禁用取消重复请求（上传可能耗时较长）
      cancelRepeat: false,
      // 设置较长的超时时间（5分钟）
      timeout: 300000,
    }
  )
}

/**
 * 获取知识库的文档列表
 */
export function getDocuments(kbId: string) {
  return request.get<KBDocument[]>(`/knowledge-bases/${kbId}/documents`, { loading: false })
}

/**
 * 删除文档
 */
export function deleteDocument(kbId: string, docId: string) {
  return request.delete(`/knowledge-bases/${kbId}/documents/${docId}`)
}

export function restoreDocument(kbId: string, docId: string) {
  return request.post<KBDocument>(`/knowledge-bases/${kbId}/documents/${docId}/restore`)
}

/**
 * 切片信息
 */
export interface ChunkInfo {
  index: number
  content: string
}

/**
 * 文档切片响应
 */
export interface DocumentChunksResponse {
  document_id: string
  filename: string
  chunk_count: number
  chunks: ChunkInfo[]
}

/**
 * 获取文档切片
 */
export function getDocumentChunks(kbId: string, docId: string) {
  return request.get<DocumentChunksResponse>(`/knowledge-bases/${kbId}/documents/${docId}/chunks`, {
    loading: false,
  })
}
