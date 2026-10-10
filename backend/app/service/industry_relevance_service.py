"""行业相关性闸 —— 判断候选资讯"是否属于本行业"

背景
----
资讯检索「同时搜全网」时，通过 rerank 阈值的全网结果会自动沉淀入库
（见 news_search_service._persist_online）。但原链路上只有两道闸：

    闸① 来源质量（source_quality_service）—— 只认"站点是否低质"，不看行业
    闸② rerank 相关度（MIN_SCORE=0.05）—— 只判"与查询词是否相关"

两者都不判断"内容是否属于化工行业"。后果（实测）：输入泛词「收购」时，
医美诊所收购、培训公司转让、B2B 处理网、股吧帖子、股票行情页等高相关度
但完全非化工的内容被沉淀进 chemical 资讯库，造成**永久污染**——下次再搜
依然会被翻出来。

设计
----
两级判定，先快后准：

1. **强信号词表**（`STRONG_TERMS`）—— 命中即放行，零成本。
   只放"出现即基本可确认是行业资讯"的词（装置、产能、检修、危化品、
   具体产品/工艺等），不放"化学""化工"这类会出现在公司名里的弱词。

2. **LLM 批量判定** —— 其余候选一次性提交（单次调用，避免逐条开销），
   明确要求排除股票行情页、招聘页、文库文档、B2B 买卖信息等。

**失败降级为"放行"**：LLM 不可用/超时/解析失败时保留全部候选。
宁可漏杀，不可误杀——闸门故障不应导致检索结果全空。

对外接口：
    kept_idx, dropped = get_industry_relevance_service().filter_relevant(items, industry_id)
    # items: [{"title": str, "summary": str}, ...]
    # kept_idx: 保留下标列表；dropped: [{"index": i, "reason": str}, ...]
"""
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# 开关：默认开启；置 0/false 可整体关闭（排查线上问题时用）
INDUSTRY_GATE_ENABLED = os.getenv("NEWS_INDUSTRY_GATE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
# 判定模型：turbo 足够，且明显快于 plus
INDUSTRY_GATE_MODEL = os.getenv("NEWS_INDUSTRY_GATE_MODEL", "qwen-turbo")
# 单批最多提交候选数（超出则分批）
INDUSTRY_GATE_BATCH = int(os.getenv("NEWS_INDUSTRY_GATE_BATCH", "30"))
# 单条文本截断长度（判定只需摘要级信息）
INDUSTRY_GATE_TEXT_LIMIT = int(os.getenv("NEWS_INDUSTRY_GATE_TEXT_LIMIT", "260"))

# ------------------------------------------------------------
# 1. 强信号词：命中即判定为"相关"，不调 LLM
#    只收"出现即基本可确认是行业资讯"的词，避免误放行。
# ------------------------------------------------------------
STRONG_TERMS = {
    # --- 生产/装置/产能 ---
    # 注意：不要收「生产线 / 产线 / 负荷 / 现货」这类通用工业词——
    # 实测 B2B 供求广告（如「【高价收购矿泉水生产线…】- 处理网」）会命中它们而漏网。
    "装置", "产能", "开工率", "检修", "停车", "停产", "复产", "投产", "试生产",
    "扩产", "改扩建", "技改", "中交", "联动试车",
    "精馏", "裂解", "聚合", "催化", "合成", "提纯",
    # --- 产品/工艺（化工专有，不做穷举，够覆盖常见查询）---
    "环氧丙烷", "聚氨酯", "聚丙烯", "聚乙烯", "聚氯乙烯", "己内酰胺", "丙烯腈",
    "苯乙烯", "乙二醇", "丙烯", "乙烯", "甲醇", "乙醇", "苯酚", "丙酮", "双酚A",
    "钛白粉", "纯碱", "烧碱", "尿素", "磷肥", "钾肥", "复合肥", "农药", "除草剂",
    "涂料", "染料", "颜料", "树脂", "助剂", "添加剂", "催化剂", "溶剂", "胶片",
    "有机硅", "氟化工", "磷化工", "煤化工", "石油化工", "精细化工", "新材料",
    "MDI", "TDI", "PTA", "PVC", "PP", "PE", "PO", "BDO", "PMMA", "ABS", "DMC",
    # --- 政策/合规/安全环保 ---
    "危化品", "危险化学品", "化工园区", "安全生产", "安全事故", "泄漏", "爆炸",
    "环保督察", "排污许可", "双碳", "碳排放", "能耗双控", "应急管理", "储罐",
    "重大危险源", "受限空间", "特种设备",
    # --- 常用指标/语境 ---
    "万吨", "标吨", "开工负荷", "价格行情", "期货交割",
}

# ------------------------------------------------------------
# 2. 确定性非资讯形态：命中即判"不相关"（无需 LLM）
#    典型是股票行情页——标题含公司名，会骗过"公司名"类查询。
# ------------------------------------------------------------
IRRELEVANT_TITLE_PATTERNS = [
    re.compile(r"[:：]\s*-?\d+(\.\d+)?\s*[+\-]?\d*(\.\d+)?%"),   # "万华化学:69.40 -0.07%"
    re.compile(r"(股吧|诊股|个股行情|实时行情|股票行情|资金流向|涨跌幅板|K线)"),
    re.compile(r"(招聘|应聘|求职|宣讲会|双选会|校园招聘)"),
    re.compile(r"(招股说明书|公司债|信用评级报告)"),
    # 二手设备 / B2B 供求广告（非行业资讯）：常带【】或"高价/大量/长期/求购"等叫卖语
    re.compile(r"[【\[][^】\]]{0,40}(收购|求购|出售|回收|转让|供应|直销)"),
    re.compile(r"(高价收购|大量收购|长期收购|库存积压|求购|现货供应|厂家直销|价格面议|低价处理|整厂回收)"),
    # B2B 信息平台后缀
    re.compile(r"[-—_|\s]\s*(处理网|顺企网|企业网|黄页88|马可波罗|环球贸易网|中国供应商)\s*$"),
]

# ------------------------------------------------------------
# 3. 行业画像（供 LLM 判定用的自然语言描述）
# ------------------------------------------------------------
INDUSTRY_PROFILES = {
    "chemical": (
        "化工行业。包含：化工产品的价格与行情（如环氧丙烷、MDI、PTA 等）、"
        "生产企业与装置的动态（投产、检修、扩产、事故）、危化品监管与安全生产政策、"
        "化工园区与项目建设、环保与双碳要求、化工新材料与工艺技术。"
    ),
}

DEFAULT_PROFILE = "该行业的企业动态、产品行情、政策法规、技术工艺与安全环保信息。"


class IndustryRelevanceService:
    """行业相关性判定（两级：强信号词 + LLM 批量判定）"""

    def __init__(self):
        self.api_key = os.getenv("DASHSCOPE_API_KEY")
        self.base_url = os.getenv(
            "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self._client = None

    def _client_or_new(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    # ---------- 单条快速判定 ----------

    @staticmethod
    def _strong_hit(text: str) -> bool:
        low = (text or "").lower()
        return any(term.lower() in low for term in STRONG_TERMS)

    @staticmethod
    def _looks_like_non_news(title: str) -> bool:
        t = title or ""
        return any(p.search(t) for p in IRRELEVANT_TITLE_PATTERNS)

    # ---------- LLM 批量判定 ----------

    def _judge_batch(self, texts: List[str], industry_id: str) -> Optional[List[bool]]:
        """对一批文本判定是否与行业相关。返回与 texts 等长的布尔列表；失败返回 None。"""
        if not texts:
            return []
        if not self.api_key:
            logger.warning("[industry_gate] DASHSCOPE_API_KEY 未配置，跳过判定（放行）")
            return None

        profile = INDUSTRY_PROFILES.get(industry_id, DEFAULT_PROFILE)
        numbered = "\n".join(
            f"{i + 1}. {(t or '')[:INDUSTRY_GATE_TEXT_LIMIT]}" for i, t in enumerate(texts)
        )
        prompt = (
            f"你是资讯垂直领域的相关性审核员。判定目标是：{profile}\n\n"
            f"下面是 {len(texts)} 条待判定内容（每条为「标题 + 摘要」）：\n"
            f"{numbered}\n\n"
            "判定标准：只有当内容**本身讲述的是该行业的经营/产品/政策/技术/安全信息**时才算相关。\n"
            "以下一律判为不相关：\n"
            "- 股票行情页、股价快照、股吧讨论、投资分析（只提到公司名不算）\n"
            "- 招聘、求职、宣讲会\n"
            "- 文库/课件/试题/范文等文档资料\n"
            "- 二手设备、B2B 求购出售、商务交易信息（非通用行业资讯）\n"
            "- 与上述行业完全无关的其他领域（如医疗美容、教育培训、法律服务的商业广告）\n\n"
            f"请只输出一个 JSON 数组，长度必须为 {len(texts)}，元素为 true（相关）或 false（不相关），"
            "例如：[true,false,true]。不要输出任何解释、不要加 ``` 标记。"
        )

        try:
            resp = self._client_or_new().chat.completions.create(
                model=INDUSTRY_GATE_MODEL,
                messages=[
                    {"role": "system", "content": "你只输出严格合法的 JSON 数组。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text.startswith("```"):
                text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
                text = re.sub(r"\s*```$", "", text)
            data = json.loads(text)
            if not isinstance(data, list) or len(data) != len(texts):
                logger.warning(
                    f"[industry_gate] 判定结果长度不符：期望 {len(texts)}，得到 "
                    f"{len(data) if isinstance(data, list) else type(data)}，放行"
                )
                return None
            return [bool(x) for x in data]
        except Exception as e:
            logger.warning(f"[industry_gate] LLM 判定失败，放行本批：{e}")
            return None

    # ---------- 对外 ----------

    def filter_relevant(
        self,
        items: List[Dict[str, Any]],
        industry_id: str = "chemical",
    ) -> Tuple[List[int], List[Dict[str, Any]]]:
        """过滤掉与行业无关的候选。

        Args:
            items: [{"title": str, "summary": str, ...}, ...]
            industry_id: 行业标识（决定判定画像）

        Returns:
            (保留的下标列表, 被丢弃项列表[{"index": int, "reason": str}])
        """
        if not items:
            return [], []
        if not INDUSTRY_GATE_ENABLED:
            return list(range(len(items))), []

        keep = [True] * len(items)
        dropped: List[Dict[str, Any]] = []
        pending: List[int] = []      # 需要送 LLM 判定
        pending_texts: List[str] = []

        for i, it in enumerate(items):
            title = (it.get("title") or "").strip()
            summary = (it.get("summary") or it.get("content") or "").strip()
            text = f"{title}\n{summary}"

            # 确定性排除：股票行情页 / 招聘 / 文库等形态
            if self._looks_like_non_news(title):
                keep[i] = False
                dropped.append({"index": i, "reason": "非资讯形态(行情/招聘/文档)", "title": title[:60]})
                continue
            # 强信号词 → 直接放行
            if self._strong_hit(text):
                continue
            pending.append(i)
            pending_texts.append(text)

        # 其余交 LLM 批量判定
        for start in range(0, len(pending), max(1, INDUSTRY_GATE_BATCH)):
            batch_idx = pending[start:start + INDUSTRY_GATE_BATCH]
            batch_texts = pending_texts[start:start + INDUSTRY_GATE_BATCH]
            verdicts = self._judge_batch(batch_texts, industry_id)
            if verdicts is None:
                continue  # 降级放行
            for i, ok in zip(batch_idx, verdicts):
                if not ok:
                    keep[i] = False
                    dropped.append({
                        "index": i,
                        "reason": "LLM 判定与行业无关",
                        "title": (items[i].get("title") or "")[:60],
                    })

        kept_idx = [i for i, k in enumerate(keep) if k]
        if dropped:
            logger.info(
                f"[industry_gate] {len(items)} 条候选 -> 保留 {len(kept_idx)} 条，"
                f"丢弃 {len(dropped)} 条（行业不相关）"
            )
        return kept_idx, dropped


_service: Optional[IndustryRelevanceService] = None


def get_industry_relevance_service() -> IndustryRelevanceService:
    global _service
    if _service is None:
        _service = IndustryRelevanceService()
    return _service
