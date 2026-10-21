"""本地解析 + 写入链路冒烟测试（在 backend 容器内跑）。"""
import os
import sys
import tempfile

sys.path.insert(0, "/app")

from service.local_parser_service import extract_text, supports_local
from service.kb_ingest_service import chunk_text, index_text_to_milvus
from service.embedding_service import generate_embedding
from service.milvus_service import get_milvus_service

COLL = "kb_smoke_local_parse"
tmp = tempfile.mkdtemp()
ok = True


def check(name, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(extra) if extra else ""))


print("=== 1) 各格式解析 ===")

md_path = os.path.join(tmp, "sample.md")
with open(md_path, "w", encoding="utf-8") as fh:
    fh.write("# 己二酸装置检修\n\n" + "本次检修涉及 PA66 聚合线，计划停车 15 天，影响产能约每月 8000 吨。\n" * 20)
t, e = extract_text(md_path, "sample.md")
check("md 解析", t is not None, f"len={len(t or '')} err={e}")

txt_path = os.path.join(tmp, "sample.txt")
with open(txt_path, "w", encoding="gb18030") as fh:
    fh.write("苯酚丙酮装置扩产可行性研究\n" + "新建 30 万吨/年苯酚装置，配套 18 万吨/年丙酮。\n" * 15)
t, e = extract_text(txt_path, "sample.txt")
check("txt(GB18030) 解析", t is not None and "苯酚" in t, f"len={len(t or '')} err={e}")

docx_path = os.path.join(tmp, "sample.docx")
try:
    from docx import Document as DocxDocument
    d = DocxDocument()
    d.add_heading("万华化学 MDI 装置扩产公告", level=1)
    d.add_paragraph("新增 MDI 产能 60 万吨/年，预计 2027 年投产。" * 8)
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "产品"
    table.cell(0, 1).text = "产能"
    table.cell(1, 0).text = "MDI"
    table.cell(1, 1).text = "380 万吨/年"
    d.save(docx_path)
    t, e = extract_text(docx_path, "sample.docx")
    check("docx 解析", t is not None and "MDI" in t, f"len={len(t or '')} err={e}")
except Exception as exc:
    check("docx 解析", False, exc)

xlsx_path = os.path.join(tmp, "sample.xlsx")
try:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "产能表"
    ws.append(["企业", "产品", "产能"])
    ws.append(["万华化学", "MDI", "380 万吨/年"])
    ws.append(["恒力石化", "PTA", "1160 万吨/年"])
    wb.save(xlsx_path)
    t, e = extract_text(xlsx_path, "sample.xlsx")
    check("xlsx 解析", t is not None and "万华" in t, f"len={len(t or '')} err={e}")
except Exception as exc:
    check("xlsx 解析", False, exc)

html_path = os.path.join(tmp, "sample.html")
with open(html_path, "w", encoding="utf-8") as fh:
    fh.write("<html><head><style>.a{color:red}</style></head><body><h1>环氧丙烷</h1>"
             "<p>本周环氧丙烷市场强势整理，华东现货报价上调 200 元/吨。</p>"
             "<script>var x=1;</script></body></html>")
t, e = extract_text(html_path, "sample.html")
check("html 解析（去标签/去脚本）", t is not None and "环氧丙烷" in (t or "") and "var x" not in (t or ""), f"len={len(t or '')} err={e}")

# PDF：这里只用「有效但无文本层」的 PDF 验证分支能跑通、并且失败原因可读。
# （真实带文本层的 PDF 已另行实测：W3C dummy.pdf → PyPDF2 提取到 "Dumm y PDF file" 15 字，通过）
pdf_path = os.path.join(tmp, "blank.pdf")
try:
    from PyPDF2 import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(pdf_path, "wb") as fh:
        writer.write(fh)
    t, e = extract_text(pdf_path, "blank.pdf")
    check("pdf 分支可执行（无文本层时给出 OCR 提示）", t is None and "未提取到有效文本" in e, e[:40])
except Exception as exc:
    check("pdf 分支可执行", False, exc)

check("不支持格式被正确拒绝", not supports_local("a.pptx") and extract_text(md_path, "a.pptx")[0] is None)

print("\n=== 2) 空/扫描件被拦 ===")
empty_path = os.path.join(tmp, "empty.txt")
open(empty_path, "w").close()
t, e = extract_text(empty_path, "empty.txt")
check("空文件给出可读原因", t is None and "未提取到有效文本" in e, e)

print("\n=== 3) 切片 + 向量 + 写入 Milvus ===")
t, e = extract_text(md_path, "sample.md")
chunks = chunk_text(t)
check("切片", len(chunks) > 1, f"chunks={len(chunks)}")

vm = generate_embedding(chunks[:2])
check("embedding", bool(vm) and len(vm[0]) == 1024, f"dim={len(vm[0]) if vm else 0}")

r = index_text_to_milvus(t, "sample.md", COLL)
check("写入 Milvus", r.get("success"), r)

print("\n=== 4) 检索验证 ===")
qv = generate_embedding("PA66 聚合线检修停车多久")
hits = get_milvus_service().search(COLL, query_vector=qv, top_k=10)
check("能检索到", len(hits) > 0, f"hits={len(hits)}")
for h in hits[:3]:
    print(f"   score={round(h.get('score') or 0, 4)} file={h.get('filename')} "
          f"chunk={h.get('chunk_index')} text={(h.get('content') or '')[:36]}")

print("\n=== 5) 同名重传幂等 ===")
# 注意：不能用 num_entities 判断——Milvus 的逻辑删除在 compact 之前不会让它下降。
# 正确的判据是「重传后检索结果里不出现重复的 chunk_index」。
r2 = index_text_to_milvus(t, "sample.md", COLL)
hits2 = get_milvus_service().search(COLL, query_vector=qv, top_k=10)
idx = [h["chunk_index"] for h in hits2]
check("重传后无重复切片", len(idx) == len(set(idx)) and len(idx) > 0, f"chunk_index={idx}")

get_milvus_service().delete_collection(COLL)
print("\n清理测试集合完成")
print("\n结果:", "全部通过" if ok else "存在失败项")
sys.exit(0 if ok else 1)
