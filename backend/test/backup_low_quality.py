"""一次性脚本：备份库中命中质量过滤规则的低质记录（只读，不删除）。

用法（backend 目录，venv 激活后）:
    python test/backup_low_quality.py
输出:
    ../backups/low_quality_news_backup_<日期>.json
"""
import datetime
import json
import os
import sys
import uuid

sys.path.insert(0, "app")

from dotenv import load_dotenv

load_dotenv(".env")

from sqlalchemy import inspect as sa_inspect

from core.database import SessionLocal
from models.news import IndustryNews
from service.source_quality_service import get_source_quality_service


def main():
    tag = ""
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--tag" and i + 1 < len(argv):
            tag = argv[i + 1].strip()

    service = get_source_quality_service()
    db = SessionLocal()
    try:
        cols = [c.key for c in sa_inspect(IndustryNews).columns]
        rows = []
        for news in db.query(IndustryNews).all():
            verdict = service.check(news.source_url or "", title=news.title or "")
            if verdict.accepted:
                continue
            item = {}
            for col in cols:
                val = getattr(news, col)
                if isinstance(val, (datetime.datetime, datetime.date)):
                    val = val.isoformat()
                elif isinstance(val, uuid.UUID):
                    val = str(val)
                item[col] = val
            item["_delete_reason"] = verdict.reason
            item["_delete_host"] = verdict.host
            rows.append(item)

        out_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "backups")
        )
        os.makedirs(out_dir, exist_ok=True)
        base = f"low_quality_news_backup_{datetime.date.today().isoformat()}"
        if tag:
            base += f"_{tag}"
        fname = os.path.join(out_dir, base + ".json")
        seq = 1
        while os.path.exists(fname):  # 绝不覆盖既有备份
            seq += 1
            fname = os.path.join(out_dir, f"{base}_{seq}.json")
        with open(fname, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)

        print("备份文件:", fname)
        print(f"备份条数: {len(rows)} | 字段数: {len(cols)} | 大小: {os.path.getsize(fname)} bytes")
    finally:
        db.close()


if __name__ == "__main__":
    main()
