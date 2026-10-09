"""基准测试：LLM 抽取并发数对端到端耗时的影响

同一批资讯，分别在并发 3 与 6 下跑一次，比较墙钟时间，
用于确定 NEWS_EXTRACT_CONCURRENCY 的合理默认值。

用法（在 backend 目录下）:
    ../.venv/Scripts/python.exe test/bench_extract_concurrency.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from service.news_extraction_service import NewsExtractionService

SAMPLES = [
    ("万华化学新建60万吨MDI装置投产", "万华化学宣布其烟台60万吨/年MDI装置正式投产，总投资约80亿元。"),
    ("应急管理部部署危化品重大危险源检查", "应急管理部印发通知，要求各地对危化品重大危险源开展全覆盖检查。"),
    ("纯碱价格本周上调50元/吨", "本周华东地区纯碱现货报价上调50元/吨，厂家库存处于低位。"),
    ("巴斯夫湛江一体化基地首套装置投产", "巴斯夫湛江一体化基地首套装置已投产，投资总额约100亿欧元。"),
    ("某化工园区发生泄漏事故", "某化工园区发生物料泄漏事故，未造成人员伤亡，已启动应急响应。"),
    ("石化行业碳达峰实施方案发布", "工信部等部门联合发布石化行业碳达峰实施方案，明确2025年目标。"),
]


async def run_batch(concurrency: int) -> float:
    service = NewsExtractionService()
    semaphore = asyncio.Semaphore(concurrency)
    done = 0

    async def worker(title, content):
        nonlocal done
        async with semaphore:
            await service.extract_one(title, content)
            done += 1

    start = time.time()
    await asyncio.gather(*(worker(t, c) for t, c in SAMPLES))
    elapsed = time.time() - start
    print(f"  并发 {concurrency}: {len(SAMPLES)} 条耗时 {elapsed:.1f}s "
          f"({elapsed / len(SAMPLES):.2f}s/条) 完成 {done} 条")
    return elapsed


async def main():
    print("=" * 60)
    print("LLM 抽取并发基准（同一批 6 条资讯）")
    print("=" * 60)
    t3 = await run_batch(3)
    t6 = await run_batch(6)
    print()
    winner = 3 if t3 < t6 else 6
    print(f"结论: 并发 {winner} 更快（3→{t3:.1f}s, 6→{t6:.1f}s）")
    print(f"建议 NEWS_EXTRACT_CONCURRENCY={winner}")


asyncio.run(main())
