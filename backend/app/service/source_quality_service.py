"""资讯来源质量过滤服务

背景
----
博查（Bocha）对"化工 政策""化工 安全事故"这类泛化关键词，会返回大量
SEO 权重极高的**文档分享站 / 题库教育站**，例如：
    豆丁网 docin.com、道客巴巴 doc88.com、原创力文档 book118.com、
    淘豆网 taodocs.com、个人图书馆 360doc.cn、
    刷刷题 shuashuati.com、1010家教 1010jiajiao.com、360问答 wenda.so.com
这些内容本质是课件、试卷、文档转载，与「化工行业情报」无关。
实测某次化工采集 47 条里，上述站点占了 27 条（57%）。

设计
----
四级判定，优先级从上到下，命中即返回：

1. **域名黑名单**（`BLOCKED_DOMAINS`）—— 直接拒绝
   文档分享、题库教育、问答社区、低质聚合。

2. **权威源白名单**（`PREFERRED_DOMAINS`）—— 直接通过并加权重分
   官方监管、行业协会、化工专业媒体、权威财经。
   **必须排在 URL/标题特征之前**：权威站的 URL 结构不可预测
   （如北极星 `/special/?id=xxx` 实际是文章页，放后会被误杀）。

3. **URL 强特征**（`BLOCKED_URL_PATTERNS`）—— 拒绝
   指向 .pdf/.ppt 文档、标签聚合页、下载页。

4. **标题强特征**（`BLOCKED_TITLE_PATTERNS`）—— 拒绝
   试题题干、教案课件、"免费下载"、"标签"聚合等。

白名单的 `score` 供后续个性化排序使用；本期只做标记与统计，不改动排序逻辑。

对外接口：
    verdict = check_source_quality(url, title, content)
    if not verdict.accepted:
        skip

维护提示：新增站点优先加进对应集合，不要在 collect 逻辑里写死判断。
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ============================================================
# 1. 域名黑名单 —— 命中即拒绝
# ============================================================
BLOCKED_DOMAINS = {
    # ---- 文档分享 / 文库（课件、试卷、文档转载）----
    "docin.com",          # 豆丁网
    "doc88.com",          # 道客巴巴
    "book118.com",        # 原创力文档
    "taodocs.com",        # 淘豆网
    "360doc.com",         # 个人图书馆
    "360doc.cn",
    "renrendoc.com",      # 人人文库
    "wenku.baidu.com",    # 百度文库
    "wenku.so.com",       # 360 文库
    "doc.mbalib.com",     # MBA 智库文档
    "mayiwenku.com",      # 蚂蚁文库
    "wendangku.com",
    "jinchutou.com",
    "docer.com",          # 百度文库客户端站
    "5doc.com",
    "cnki.net",           # 知网：学术论文，非行业资讯
    "wanfangdata.com.cn",
    "cqvip.com",
    # ---- 题库 / 教育 / 考试 ----
    "shuashuati.com",     # 刷刷题
    "1010jiajiao.com",    # 1010 家教网
    "jyeoo.com",          # 菁优网
    "zxxk.com",           # 学科网
    "xkw.com",            # 组卷网
    "21cnjy.com",         # 21 世纪教育网
    "zybang.com",         # 作业帮
    "jiajiao.com",        # 家教网
    "chachaba.com",       # 查字典
    "5ykj.com",           # 五优课件
    "1kejian.com",
    "kejian.tv",
    "jiaoxue.com",
    "zujuan.com",
    "tiku.com",
    # ---- 问答 / 社区低质 ----
    "wenda.so.com",       # 360 问答
    "zhidao.baidu.com",   # 百度知道
    "iask.sina.com.cn",   # 爱问
    "wenwen.sogou.com",   # 搜狗问问
    "zhihu.com",          # 知乎：观点问答，非一手资讯
    "tieba.baidu.com",    # 贴吧
    # ---- 低质聚合 / 采集站 ----
    "yhs518.com",         # 货源网
    "chiyulj.com",
    "soyjg.com",          # 仪表网（内容多为低质转载）
    "aqrzj.com",
    "doc.qq.com",
}

# ============================================================
# 2. URL 特征 —— 命中即拒绝
# ============================================================
BLOCKED_URL_PATTERNS = [
    re.compile(r"\.(pdf|ppt|pptx|doc|docx|xls|xlsx|zip|rar|txt)(\?|#|$)", re.I),
    re.compile(r"/(tag|tags|label|labels|zt|special|topic|search)(/|$|\?)", re.I),
    re.compile(r"/(down|download|doc|wenku|paper)(/|$)", re.I),
    re.compile(r"[?&](q|wd|kw|query)=", re.I),
]

# ============================================================
# 3. 标题强特征 —— 命中即拒绝
#    只放"新闻标题绝不可能出现"的特征，避免误杀
# ============================================================
BLOCKED_TITLE_PATTERNS = [
    # 试题形态
    re.compile(r"[（(]\s*[　\s]{0,4}[）)]"),                      # 空括号题干
    re.compile(r"^\s*\d{1,2}\s*[．.、]\s*\S"),                    # "15．与氨碱法比较…"
    re.compile(r"(单选题|多选题|判断题|填空题|选择题|简答题|问答题|计算题|应用题)"),
    re.compile(r"(参考答案|正确答案|答案与解析|试题答案|答案解析)"),
    re.compile(r"(试卷|真题|模拟题|练习题|习题集|课后习题|考试题|题库|期末|期中|单元测试)"),
    # 教学资料
    re.compile(r"(教案|课件|讲义|说课|教学设计|公开课|ppt\b|PPT)"),
    # 文档站/下载站
    re.compile(r"(免费下载|文档下载|下载文档|在线阅读|全文阅读|资源下载)"),
    re.compile(r"(个人图书馆|范文|心得体会|工作总结|汇报材料)"),
    # 站点后缀
    re.compile(r"[-—_|]\s*(豆丁网|道客巴巴|原创力文档|淘豆网|人人文库|百度文库|刷刷题APP|个人图书馆)\s*$"),
    # 标签聚合页
    re.compile(r"(^|[_\-|])标签([_\-|]|$)"),
]

# ============================================================
# 4. 权威源白名单 —— 通过并加权重分
# ============================================================
PREFERRED_DOMAINS = {
    # 官方 / 监管 / 协会
    "gov.cn": 1.5,
    "ndrc.gov.cn": 2.0,       # 发改委
    "mee.gov.cn": 2.0,        # 生态环境部
    "mem.gov.cn": 2.0,        # 应急管理部
    "miit.gov.cn": 2.0,       # 工信部
    "stats.gov.cn": 2.0,      # 统计局
    "cpcif.org.cn": 2.0,      # 中国石油和化学工业联合会
    "cnlic.com.cn": 1.5,      # 中国轻工业联合会
    # 化工专业媒体 / 数据商
    "ccin.com.cn": 2.0,       # 中国化工报 / 中化新网
    "oilchem.net": 2.0,       # 隆众资讯（价格行情）
    "sci99.com": 2.0,         # 卓创资讯
    "315i.com": 2.0,          # 金联创
    "100ppi.com": 1.5,        # 生意社（大宗价格）
    "chem99.com": 1.5,        # 化工网
    "hg707.com": 1.5,         # 化工 707
    "bjx.com.cn": 1.5,        # 北极星（环保/能源）
    "chemnews.com.cn": 1.5,
    # 上市公司公告 / 交易所
    "cninfo.com.cn": 2.0,     # 巨潮资讯
    "sse.com.cn": 1.5,
    "szse.cn": 1.5,
    # 权威财经
    "cnstock.com": 1.5,       # 上海证券报
    "stcn.com": 1.5,          # 证券时报
    "cls.cn": 1.5,            # 财联社
    "yicai.com": 1.5,
    "caixin.com": 1.5,
    "21jingji.com": 1.5,
    "jiemian.com": 1.5,
    # 主流媒体
    "xinhuanet.com": 1.5,
    "news.cn": 1.5,
    "people.com.cn": 1.5,
    "cctv.com": 1.5,
    "thepaper.cn": 1.5,
}

TIER_PREFERRED = "preferred"
TIER_NORMAL = "normal"
TIER_BLOCKED = "blocked"


@dataclass
class QualityVerdict:
    """来源质量判定结果"""

    accepted: bool
    tier: str = TIER_NORMAL
    score: float = 1.0
    reason: str = ""
    host: str = ""
    matched: Optional[str] = field(default=None)

    def __bool__(self) -> bool:
        return self.accepted


def _host_of(url: str) -> str:
    """提取小写 host，去掉 www. 前缀便于匹配"""
    try:
        host = urlparse(url or "").netloc.lower().split(":")[0]
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _domain_hit(host: str, domains) -> Optional[str]:
    """host 命中集合中的域名（含子域）则返回该域名"""
    for d in domains:
        if host == d or host.endswith("." + d):
            return d
    return None


class SourceQualityService:
    """资讯来源质量过滤器"""

    def __init__(self):
        self.blocked_domains = BLOCKED_DOMAINS
        self.preferred_domains = PREFERRED_DOMAINS

    def check(self, url: str, title: str = "", content: str = "") -> QualityVerdict:
        """判定一条搜索结果是否值得入库

        Args:
            url: 来源链接（必填）
            title: 标题
            content: 正文/摘要（当前不参与判定，预留）

        Returns:
            QualityVerdict，`accepted=False` 表示应丢弃
        """
        host = _host_of(url)
        title = (title or "").strip()

        # ---- 1. 域名黑名单（最高优先级，命中即拒）----
        hit = _domain_hit(host, self.blocked_domains)
        if hit:
            return QualityVerdict(
                accepted=False, tier=TIER_BLOCKED, score=0.0,
                reason=f"黑名单域名({hit})", host=host, matched=hit,
            )

        # ---- 2. 权威源白名单（优先于 URL/标题特征）----
        # 权威站的 URL 结构不可预测（如北极星 /special/?id=xxx 其实是文章页），
        # 若让通用 URL 特征先行会误杀，因此白名单必须放在特征判定之前。
        pref = _domain_hit(host, self.preferred_domains.keys())
        if pref:
            return QualityVerdict(
                accepted=True, tier=TIER_PREFERRED,
                score=self.preferred_domains[pref], reason="权威源",
                host=host, matched=pref,
            )

        # ---- 3. URL 特征 ----
        for pattern in BLOCKED_URL_PATTERNS:
            if pattern.search(url or ""):
                return QualityVerdict(
                    accepted=False, tier=TIER_BLOCKED, score=0.0,
                    reason=f"URL 特征({pattern.pattern[:32]})", host=host,
                    matched=pattern.pattern,
                )

        # ---- 4. 标题强特征 ----
        for pattern in BLOCKED_TITLE_PATTERNS:
            if pattern.search(title):
                return QualityVerdict(
                    accepted=False, tier=TIER_BLOCKED, score=0.0,
                    reason=f"标题特征({pattern.pattern[:32]})", host=host,
                    matched=pattern.pattern,
                )

        return QualityVerdict(accepted=True, tier=TIER_NORMAL, score=1.0, host=host)


# 模块级单例
_service: Optional[SourceQualityService] = None


def get_source_quality_service() -> SourceQualityService:
    global _service
    if _service is None:
        _service = SourceQualityService()
    return _service
