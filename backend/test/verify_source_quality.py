"""验证脚本：来源质量过滤 + Bocha freshness 参数

用法（在 backend 目录下）:
    ../.venv/Scripts/python.exe test/verify_source_quality.py

内容:
1. 质量过滤器单元用例（黑名单域名 / URL 特征 / 标题特征 / 权威源白名单）
2. 对库内已有资讯做一次真实扫描，统计会被过滤掉多少条
3. 探测 Bocha API 是否接受 freshness 参数（只读，不入库）
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from service.source_quality_service import get_source_quality_service

PASS = 0
FAIL = 0


def expect(url, title, accepted, label):
    global PASS, FAIL
    verdict = service.check(url, title=title)
    ok = verdict.accepted == accepted
    mark = "✅" if ok else "❌"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(
        f"  {mark} [{label}] accepted={verdict.accepted} tier={verdict.tier} "
        f"reason={verdict.reason or '-'} | {title[:38]}"
    )


service = get_source_quality_service()

print("=" * 72)
print("1. 质量过滤器判定用例")
print("=" * 72)
print("-- 应拒绝：文档分享站 --")
expect("https://www.docin.com/p-123.html", "氯碱法处理低浓度含氰废水 - 豆丁网", False, "豆丁网")
expect("https://max.book118.com/html/2024/x.shtm", "双碳技术进展情况汇报 .pdf-原创力文档", False, "原创力文档")
expect("https://www.doc88.com/p-999.html", "法分析氯碱中的多元素 - 道客巴巴", False, "道客巴巴")
expect("https://www.taodocs.com/p-1.html", "为避免伤亡事故发生-浙大个人主页系统.ppt_淘豆网", False, "淘豆网")
expect("https://www.360doc.cn/article/1.html", "化工安全培训资料", False, "个人图书馆")

print("-- 应拒绝：题库/教育站 --")
expect("https://www.shuashuati.com/t/1", "\"氨碱法\"是工业上用食盐制纯碱的一种常用方法-刷刷题APP", False, "刷刷题")
expect("https://m.1010jiajiao.com/timu/1", "15．与氨碱法比较，下列关于联合制碱法优点的判断中不正确的是（　　）", False, "1010家教")

print("-- 应拒绝：URL/标题特征 --")
expect("https://www.gov.cn/zhengce/content/1.htm", "关于印发石化行业碳达峰实施方案的通知", True, "政策原文(放行)")
expect("https://x.com/a/b.pdf", "化工园区安全整治提升方案", False, "pdf直链")
expect("https://news.163.com/tag/shuangtan", "双碳_标签_网易出品", False, "标签聚合页")
expect("https://www.yhs518.com/p/1", "第10题 化工原理课后习题答案", False, "习题+答案")

print("-- 应放行：权威/正常源 --")
expect("https://www.ccin.com.cn/detail/1", "万华化学60万吨MDI装置投产", True, "中国化工报")
expect("https://www.mem.gov.cn/xw/yjglbgzdt/", "应急管理部部署危化品重大危险源检查", True, "应急管理部")
expect("https://top.100ppi.com/price/1.html", "纯碱价格行情:本周现货报价上调", True, "生意社")
expect("https://www.cninfo.com.cn/new/disclosure/1", "XX化工:关于全资子公司投产的公告", True, "巨潮公告")
expect("https://finance.sina.com.cn/stock/1.shtml", "化工板块午后拉升 多股涨停", True, "新浪财经")
expect("https://www.some-random-blog.com/p/1", "某化工园区扩建项目环评公示", True, "普通源(放行)")

print()
print("=" * 72)
print("2. 库内已有资讯扫描（只读）")
print("=" * 72)
from core.database import SessionLocal
from service.news_collection_service import NewsCollectionService

db = SessionLocal()
try:
    scan = NewsCollectionService(db).scan_low_quality_news()
    print(f"  资讯总数        : {scan['total']}")
    print(f"  命中低质        : {scan['low_quality']}"
          f"  ({scan['low_quality'] / scan['total'] * 100:.1f}%)" if scan['total'] else "")
    print(f"  按域名          : {scan['by_domain']}")
    print(f"  按原因          : {scan['by_reason']}")
    print("  样例:")
    for s in scan["samples"][:6]:
        print(f"    - {(s['title'] or '')[:50]}")
finally:
    db.close()

print()
print("=" * 72)
print("3. Bocha freshness 参数探测（只读，不入库）")
print("=" * 72)
import httpx
import os


async def probe():
    key = os.getenv("BOCHA_API_KEY", "")
    if not key:
        print("  BOCHA_API_KEY 未配置，跳过")
        return
    url = "https://api.bochaai.com/v1/web-search"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for fresh in ["oneMonth", "noLimit"]:
        payload = {"query": "化工 安全事故", "summary": True, "count": 10, "page": 1}
        if fresh != "noLimit":
            payload["freshness"] = fresh
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(url, headers=headers, json=payload)
                body = r.json()
                vals = body.get("data", {}).get("webPages", {}).get("value", []) or []
                kept = sum(
                    1 for v in vals
                    if service.check(v.get("url", ""), title=v.get("name", "")).accepted
                )
                print(f"  freshness={fresh:8s} HTTP {r.status_code} 返回 {len(vals)} 条，"
                      f"质量过滤后保留 {kept} 条")
                for v in vals[:5]:
                    vd = service.check(v.get("url", ""), title=v.get("name", ""))
                    flag = "保留" if vd.accepted else f"丢弃({vd.reason})"
                    print(f"      [{flag}] {(v.get('name') or '')[:46]}")
        except Exception as e:
            print(f"  freshness={fresh} 异常: {type(e).__name__}: {e}")


asyncio.run(probe())

print()
print("=" * 72)
print(f"用例结果: 通过 {PASS} / 失败 {FAIL}")
print("=" * 72)
sys.exit(1 if FAIL else 0)
