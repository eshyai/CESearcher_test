"""知识库写入链路：纯文本 → 切片 → 向量 → Milvus。

只有这一处实现「怎么把内容写进向量库」，两条解析路径共用它：
    - 本地解析：service/local_parser_service.py（不依赖任何云服务）
    - DocMind 解析：service/docmind_service.py（扫描件 / 老格式兜底）

共用是为了避免两份写库实现各自漂移（改维度、加字段时只改一处）。
"""
import hashlib
import logging
from typing import Any, Dict, List

from service.embedding_service import generate_embedding
from service.milvus_service import get_milvus_service

logger = logging.getLogger(__name__)


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """将文本切分成块（尽量在句末断开，块间保留 overlap 字重叠）。"""
    if not text:
        return []

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]

        # 尝试在句子边界切分
        if end < len(text):
            for sep in ['。', '！', '？', '.', '!', '?', '\n']:
                last_sep = chunk.rfind(sep)
                if last_sep > chunk_size // 2:
                    chunk = chunk[:last_sep + 1]
                    end = start + last_sep + 1
                    break

        if chunk.strip():
            chunks.append(chunk.strip())

        start = end - overlap

    return chunks


def index_text_to_milvus(
    text: str,
    file_name: str,
    index_name: str,
    chunk_size: int = 500,
) -> Dict[str, Any]:
    """把一段纯文本切块、向量化、写入 ``index_name`` 集合。

    写入前会先按 ``doc_id`` 清掉同名文档的旧切片，因此**重复上传同一文件名是幂等的**
    （内容改了重传不会新旧叠加）。

    Returns:
        {"success": bool, "message": str, "document_count": int}
    """
    result: Dict[str, Any] = {"success": False, "message": "", "document_count": 0}

    chunks = chunk_text(text, chunk_size=chunk_size)
    if not chunks:
        result["message"] = "文档切分失败（正文为空）"
        return result

    logger.info(f"[kb_ingest] {file_name}: 切出 {len(chunks)} 个切片，开始向量化")

    embeddings = generate_embedding(chunks)
    if not embeddings:
        result["message"] = "向量生成失败：embedding 接口返回为空（检查 DASHSCOPE_API_KEY / 网络）"
        return result
    if len(embeddings) != len(chunks):
        result["message"] = f"向量生成失败：数量不匹配（{len(embeddings)} vs {len(chunks)}）"
        return result

    # doc_id 只由文件名决定（沿用历史口径），保证同名重传能定位到旧切片
    doc_id = hashlib.md5(file_name.encode()).hexdigest()

    documents = []
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        chunk_id = hashlib.md5(f"{file_name}_{i}_{chunk[:50]}".encode()).hexdigest()
        documents.append({
            "id": chunk_id,
            "doc_id": doc_id,
            "kb_id": index_name,
            "filename": file_name,
            "content": chunk,
            "chunk_index": i,
            "vector": embedding,
        })

    milvus = get_milvus_service()
    milvus.create_collection(index_name)

    # 同名文档重传：先清旧切片，避免新旧版本同时被检索到
    try:
        milvus.delete_by_doc_id(index_name, doc_id)
    except Exception as exc:  # 集合原本为空 / 首次写入时可能报错，不影响后续插入
        logger.debug(f"[kb_ingest] 清理旧切片跳过: {exc}")

    logger.info(f"[kb_ingest] 写入 Milvus 集合 {index_name}，{len(documents)} 条")
    milvus.insert_documents(index_name, documents)

    result["success"] = True
    result["message"] = f"成功处理 {len(documents)} 个切片"
    result["document_count"] = len(documents)
    return result
