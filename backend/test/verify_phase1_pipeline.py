"""
阶段1管道验证脚本 - 只读验证，不连接数据库、不调用外部 API

验证内容：
1. dedup_service：SimHash 指纹计算、近重复判定、不相关文本距离
2. news_extraction_service：LLM 输出的健壮 JSON 解析与枚举规整
3. news_pipeline / worker_main：模块可导入、管道函数签名正确
"""
import asyncio
import importlib
import sys
import types

APP_DIR = "D:/chemical-agent/industry-information-assistant-master/backend/app"
sys.path.insert(0, APP_DIR)

# service/__init__.py 会级联导入 llama_index 等重型依赖，与本验证无关。
# 注册一个带 __path__ 的轻量替身包，绕过其 __init__，子模块仍按真实路径加载。
_service_stub = types.ModuleType("service")
_service_stub.__path__ = [APP_DIR + "/service"]
sys.modules.setdefault("service", _service_stub)

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        failures.append(name)


print("=" * 60)
print("1. 去重指纹服务")
print("=" * 60)
from service.dedup_service import NewsDedupService

dedup = NewsDedupService()

t1_title = "万华化学MDI装置突发故障停车"
t1_content = "万华化学烟台工业园MDI一体化装置今日凌晨发生故障临时停车，预计检修一周，或影响国内MDI市场供应。"
t2_title = "万华化学MDI装置突发故障停车，预计检修一周"  # 同事件改写（标题主干相同）
t2_content = "据报道，万华化学烟台MDI装置因故障临时停产检修，持续约一周时间，MDI供应端收紧。"
t3_title = "工信部发布化工行业碳达峰实施方案"
t3_content = "工信部印发《化工行业碳达峰实施方案》，要求到2030年化工行业碳排放强度明显下降，绿色化工占比提升。"
# 转载微调：正文几乎相同，只加了前缀和标点改动
t4_title = "东华能源丙烷脱氢项目投产"
t4_content = "东华能源张家港PDH二期项目今日正式投产，年产能60万吨，丙烷脱氢制丙烯成本优势明显。"
t5_title = "东华能源丙烷脱氢项目投产"
t5_content = "【转载】东华能源张家港PDH二期项目今日正式投产，年产能60万吨，丙烷脱氢制丙烯成本优势明显。"

fp1 = dedup.compute_fingerprint(t1_title, t1_content)
fp2 = dedup.compute_fingerprint(t2_title, t2_content)
fp3 = dedup.compute_fingerprint(t3_title, t3_content)
fp4 = dedup.compute_fingerprint(t4_title, t4_content)
fp5 = dedup.compute_fingerprint(t5_title, t5_content)

check("指纹为16位十六进制", len(fp1) == 16 and int(fp1, 16) >= 0, f"fp1={fp1}")
d13 = dedup.hamming_distance(fp1, fp3)
d45 = dedup.hamming_distance(fp4, fp5)
print(f"    不相关距离 d13={d13}, 转载微调距离 d45={d45}")
check("转载微调判为近重复(SimHash)", d45 <= dedup.hamming_threshold, f"d45={d45}")
check("不相关文本不误判", d13 > dedup.hamming_threshold, f"d13={d13}")
check("空文本指纹稳定", dedup.compute_fingerprint("", "") == "0" * 16)

# 同事件改写由标题 Jaccard 抓住
j12 = dedup._title_jaccard(t1_title, t2_title)
j13 = dedup._title_jaccard(t1_title, t3_title)
print(f"    同事件标题相似度 j12={j12:.2f}, 不相关标题相似度 j13={j13:.2f}")
check("同事件改写由标题相似度命中", j12 >= dedup.title_jaccard_threshold, f"j12={j12}")
check("不相关标题不误判", j13 < dedup.title_jaccard_threshold, f"j13={j13}")

print()
print("=" * 60)
print("2. LLM 抽取结果解析")
print("=" * 60)
from service.news_extraction_service import NewsExtractionService, EVENT_TYPES

parse = NewsExtractionService._parse_llm_json

# 模拟带代码块和杂文的 LLM 输出
raw_llm = """好的，以下是分析结果：
```json
{"topics": [{"type": "company", "name": "万华化学"}, {"type": "product", "name": "MDI"}],
 "event_type": "停产检修",
 "impact_direction": "negative",
 "key_numbers": [{"name": "检修周期", "value": "7", "unit": "天"}],
 "entities": ["万华化学", "烟台工业园"],
 "summary": "万华化学MDI装置故障停车约一周，供应收紧"}
```
希望对你有帮助。"""

parsed = parse(raw_llm)
check("能剥离代码块和杂文", parsed is not None)
if parsed:
    check("主题标签解析", len(parsed["topics"]) == 2 and parsed["topics"][0]["name"] == "万华化学")
    check("事件类型合法", parsed["event_type"] in EVENT_TYPES and parsed["event_type"] == "停产检修")
    check("影响方向合法", parsed["impact_direction"] == "negative")
    check("关键数字解析", parsed["key_numbers"][0]["value"] == "7")

# 非法枚举值降级
bad = parse('{"topics": [{"type": "foo", "name": "x"}], "event_type": "不存在的类型", "impact_direction": "up"}')
check("非法 type 降级为 industry", bad is not None and bad["topics"][0]["type"] == "industry")
check("非法事件类型降级为 其他", bad is not None and bad["event_type"] == "其他")
check("非法影响方向降级为 neutral", bad is not None and bad["impact_direction"] == "neutral")
check("完全非 JSON 返回 None", parse("模型抽风了没有输出json") is None)
check("空输入返回 None", parse("") is None)

print()
print("=" * 60)
print("3. 管道与 Worker 模块导入")
print("=" * 60)
import inspect

import service.news_pipeline as pipeline_mod
check("news_pipeline 可导入", hasattr(pipeline_mod, "run_full_pipeline"))
sig = inspect.signature(pipeline_mod.run_full_pipeline)
check("管道签名含去重/抽取相关参数",
      {"max_news", "extraction_limit", "industry_id", "focus_user_id"}.issubset(set(sig.parameters)))

import worker_main
check("worker_main 可导入", hasattr(worker_main, "main") and hasattr(worker_main, "run_pipeline_once"))

import models.news as news_mod
cols = {c.name for c in news_mod.IndustryNews.__table__.columns}
check("模型含去重列", {"content_fingerprint", "is_duplicate", "duplicate_of_id"}.issubset(cols))
check("模型含抽取列", {"extracted_topics", "event_type", "impact_direction",
                        "key_numbers", "entities", "summary",
                        "extraction_status", "extracted_at"}.issubset(cols))

print()
print("=" * 60)
if failures:
    print(f"结果: {len(failures)} 项失败 -> {failures}")
    sys.exit(1)
print("结果: 全部通过")
