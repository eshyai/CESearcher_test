"""本地文档解析：不依赖 DocMind / 任何云服务，把文件直接读成纯文本。

为什么要它：
    文档入库原先只有 DocMind 一条解析链路，一旦 AccessKey 没配（或失效），
    **任何格式都传不进来**，知识库只剩空壳。纯文本类与常见的办公文档其实本地
    就能解析到位，没必要为了它们把整条链路卡在云服务上。

支持的格式（全部对应 requirements.docker.txt 里已锁定的依赖）：
    .txt .md .csv .json .xml .yaml .yml .py .js .ts  → 按编码嗅探直接读
    .html .htm                                       → BeautifulSoup 去标签
    .pdf                                             → PyPDF2 逐页提取
    .docx                                            → python-docx（段落 + 表格）
    .xlsx                                            → openpyxl（逐 sheet 逐行）

明确不支持（需 OCR / 专有格式，仍走 DocMind）：
    .doc .ppt .pptx .xls .jpg .png 等图片

对外只有一个入口：``extract_text(path, file_name) -> (text, error)``。
text 为 None 时 error 说明原因，上层据此决定是否回退 DocMind，或把原因写进
``documents.error_message`` 让用户在界面上看到，而不是笼统一句「处理失败」。
"""
import logging
import os
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 逐行读成纯文本（编码嗅探）
PLAIN_EXTS = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml",
    ".yaml", ".yml", ".py", ".js", ".ts", ".log",
}
HTML_EXTS = {".html", ".htm"}
LOCAL_SUPPORTED = PLAIN_EXTS | HTML_EXTS | {".pdf", ".docx", ".xlsx"}

# 少于这么多字符视为「没解析出内容」。扫描件 / 图片型 PDF 通常提取到 0 字符，
# 门槛压到 10 是为了不误杀「只有一行的短文档」（实测 W3C dummy.pdf 全文 15 字）。
MIN_TEXT_CHARS = 10

# Excel 最多读多少行（防止超大表格把内存打满）
MAX_SHEET_ROWS = 20000


def supports_local(file_name: str) -> bool:
    """这个文件名能否走本地解析。"""
    return _ext(file_name) in LOCAL_SUPPORTED


def _ext(file_name: str) -> str:
    return os.path.splitext(file_name or "")[1].lower()


def _decode(raw: bytes) -> str:
    """按常见编码依次尝试。中文文档里 gb18030 覆盖 GBK/GB2312。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _normalize(text: str) -> str:
    """去掉零宽字符、行尾空格，并把连续空行压成一行。"""
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[ \t\u3000]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _read_plain(path: str) -> str:
    with open(path, "rb") as fh:
        return _decode(fh.read())


def _read_html(path: str) -> str:
    from bs4 import BeautifulSoup

    with open(path, "rb") as fh:
        raw = _decode(fh.read())
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n")


def _read_pdf(path: str) -> str:
    from PyPDF2 import PdfReader

    reader = PdfReader(path)
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:
            raise ValueError("PDF 已加密，无法本地解析")
    parts = []
    for idx, page in enumerate(reader.pages):
        try:
            parts.append(page.extract_text() or "")
        except Exception as exc:  # 单页坏掉不影响整本
            logger.warning(f"[local_parser] PDF 第 {idx + 1} 页解析失败: {exc}")
    return "\n".join(parts)


def _read_docx(path: str) -> str:
    from docx import Document as DocxDocument

    doc = DocxDocument(path)
    parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _read_xlsx(path: str) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    parts = []
    try:
        for sheet in wb.worksheets:
            parts.append(f"## {sheet.title}")
            for i, row in enumerate(sheet.iter_rows(values_only=True)):
                if i >= MAX_SHEET_ROWS:
                    parts.append("...(表格过长，已截断)")
                    break
                cells = ["" if v is None else str(v).strip() for v in row]
                if any(cells):
                    parts.append(" | ".join(cells))
    finally:
        wb.close()
    return "\n".join(parts)


_READERS = {
    **{ext: _read_plain for ext in PLAIN_EXTS},
    **{ext: _read_html for ext in HTML_EXTS},
    ".pdf": _read_pdf,
    ".docx": _read_docx,
    ".xlsx": _read_xlsx,
}


def extract_text(file_path: str, file_name: str) -> Tuple[Optional[str], str]:
    """把文件解析成纯文本。

    Returns:
        (text, error)。成功时 error 为空串；失败时 text 为 None，error 说明原因
        （原因会原样写入 ``documents.error_message``，用户能在界面上看到）。
    """
    ext = _ext(file_name)
    reader = _READERS.get(ext)
    if reader is None:
        return None, f"本地解析不支持 {ext or '该'} 格式，需要 DocMind"

    if not os.path.exists(file_path):
        return None, "上传的临时文件不存在（可能已被清理）"

    try:
        text = _normalize(reader(file_path))
    except Exception as exc:
        logger.warning(f"[local_parser] 解析 {file_name} 失败: {exc}")
        return None, f"本地解析失败: {exc}"

    if len(text) < MIN_TEXT_CHARS:
        return None, (
            "未提取到有效文本（可能是扫描件 / 图片型 PDF，或文件本身是空的），"
            "这类文件需要 OCR，暂时只能在配置好 DocMind 后处理"
        )
    return text, ""
