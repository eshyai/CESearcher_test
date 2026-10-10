"""标题判重回归测试 —— 检索结果合并与入库去重共用同一口径。

为什么需要这个测试：标题相似度阈值存在**极窄的可分区间**。
实测同一篇财新稿的两个站点版本 Jaccard=0.730，而
「PA6巴斯夫…塑胶颗粒」与「PA66巴斯夫…尼龙颗粒」=0.725、
「己内酰胺市场价格走势分析-2026年9月」与「…-2026年8月」=0.789。
单纯调 Jaccard 必然误杀其中一类，因此主力规则是「主干 + 站点后缀」，
Jaccard 只做保守兜底（0.85）。任何改动都必须跑这个文件确认没有回归。

用法（backend 目录）:
    python test/test_title_dedup.py
"""
import os
import sys

# 兼容两种跑法：宿主机 `cd backend && python test/test_title_dedup.py`，
# 以及容器内直接 `python /tmp/test_title_dedup.py`（此时 app 目录已是 /app）。
_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
for _p in (_APP_DIR, "app", "."):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from service.dedup_service import (  # noqa: E402
    looks_like_site_tail,
    normalize_title,
    title_jaccard,
    titles_same_article,
)

THRESHOLD = 0.85

PA6 = "PA6巴斯夫(德国Ultramid®)塑胶颗粒 - BASF(官方)代理商 | 顺企网公司黄页"
PA66 = "PA66巴斯夫(德国Ultramid®)尼龙颗粒 - BASF(官方)代理商 | 顺企网公司黄页"
BASF_AGENT = "德国巴斯夫(授权)代理商 | 顺企网"
CAIXIN_A = "巴斯夫上海一化工装置发生泄漏事故 聚氨酯核心原料价格受扰动_公司频道_财新网"
CAIXIN_B = "巴斯夫上海一化工装置发生泄漏事故 聚氨酯核心原料价格受扰动_财新网_财新网"
YINGCHUANG_SINA = "德国化工整合升温:巴斯夫拟收购赢创,双方确认已初步接触_新浪财经_新浪网"
YINGCHUANG_IFENG = "德国化工整合升温:巴斯夫拟收购赢创,双方确认已初步接触_大风号_凤凰网"
YINGCHUANG_EM = "德国化工整合升温:巴斯夫拟收购赢创,双方确认已初步接触 _ 东方财富网"
LEAK = "巴斯夫上海一化工装置发生泄漏事故"
EXPLODE = "巴斯夫上海一化工装置发生爆炸事故"
MONTH_A = "己内酰胺市场价格走势分析-2026年9月"
MONTH_B = "己内酰胺市场价格走势分析-2026年8月"
REGION_A = "环氧丙烷市场价格周报 华东地区"
REGION_B = "环氧丙烷市场价格周报 华南地区"
QUOTE_A = "市场行情 - 中国化学品交易网"
QUOTE_B = "市场行情 - 生意社"
PO_A = "环氧丙烷日评继续拉涨走高市场偏强上行20260911我的钢铁网"
PO_B = "环氧丙烷日评阶段性止跌回缓市场平稳消化20260922我的钢铁网"
TRANS_A = "走好化工高质量发展转型路_中国经济网--国家经济门户"
TRANS_B = "走好化工高质量发展转型路 - 经济观察网 - 专业财经新闻网站"
ADU_A = "10月1日己二酸现货报价10100元/吨 近一周累计上涨366.67元/吨"
ADU_B = "10月2日己二酸现货报价10100元/吨 近一周累计上涨366.67元/吨"

# (标题A, 标题B, 期望是否同一条, 说明)
CASES = [
    (YINGCHUANG_SINA, YINGCHUANG_IFENG, True, "门户转载：新浪 vs 凤凰（主干+站点后缀）"),
    (YINGCHUANG_SINA, YINGCHUANG_EM, True, "门户转载：新浪 vs 东财"),
    (YINGCHUANG_IFENG, YINGCHUANG_EM, True, "门户转载：凤凰 vs 东财"),
    (CAIXIN_A, CAIXIN_B, True, "同一文章两个 URL 变体"),
    (PA6, PA66, False, "不同牌号：PA6 vs PA66（Jaccard 0.725，接近阈值）"),
    (PA6, BASF_AGENT, False, "同一 B2B 站不同产品页"),
    (LEAK, EXPLODE, False, "同前缀但事件不同：泄漏 vs 爆炸"),
    (MONTH_A, MONTH_B, False, "同题不同月份（尾部含数字，不视为站点名）"),
    (REGION_A, REGION_B, False, "同题不同地区"),
    (QUOTE_A, QUOTE_B, False, "短主干「市场行情」不同站点"),
    (PO_A, PO_B, False, "环氧丙烷日评不同日期"),
    (TRANS_A, TRANS_B, False, "同标题不同门户（主干短于下限，保守不合并）"),
    (ADU_A, ADU_B, False, "同格式日报不同日期"),
]


def main() -> int:
    print(f"{'结果':<6}{'期望':<7}{'Jaccard':<10}说明")
    print("-" * 88)
    failed = 0
    for title_a, title_b, expected, label in CASES:
        got = titles_same_article(title_a, title_b, THRESHOLD)
        ok = got == expected
        failed += 0 if ok else 1
        jac = title_jaccard(normalize_title(title_a), normalize_title(title_b))
        print(f"{'OK' if ok else 'FAIL':<6}{str(expected):<7}{jac:<10.3f}{label}")

    print("-" * 88)
    print("站点后缀识别抽样（用于确认不会把日期/地区当站点名）：")
    for title, expect in (
        (YINGCHUANG_SINA, True),
        (CAIXIN_A, True),
        (MONTH_A, False),
        (REGION_A, False),
        (PO_A, False),
    ):
        from service.dedup_service import split_title

        _, tail = split_title(title)
        actual = looks_like_site_tail(tail)
        flag = "OK" if actual == expect else "FAIL"
        failed += 0 if actual == expect else 1
        print(f"  {flag}  尾部={tail!r} 判定为站点名={actual}（期望 {expect}）")

    print(f"\n失败 {failed} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
