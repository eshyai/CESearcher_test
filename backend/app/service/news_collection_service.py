"""
行业资讯和招投标信息采集服务
- 使用 Bocha API 搜索行业资讯
- 使用 81API 搜索招投标信息
- 支持多行业配置
"""
import os
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import httpx
from sqlalchemy.orm import Session

from models.news import IndustryNews, BiddingInfo, NewsCollectionTask, FocusTopic, NewsHidden
from service.bidding_service import get_bidding_service
from service.dedup_service import get_dedup_service
from service.source_quality_service import get_source_quality_service
from config.industry_config import (
    get_industry_config,
    get_all_industries,
    DEFAULT_INDUSTRY_ID,
)

logger = logging.getLogger(__name__)

# 博查搜索单次请求条数上限（配合质量过滤做「多取少留」）
BOCHA_MAX_COUNT = 30
# 博查时间范围过滤：默认为近一个月，显著降低陈旧 SEO 文档占比
# 可选值：oneDay / oneWeek / oneMonth / oneYear / noLimit
BOCHA_FRESHNESS = os.getenv("BOCHA_FRESHNESS", "oneMonth")


class NewsCollectionService:
    """资讯采集服务"""

    def __init__(self, db: Session):
        self.db = db
        self.bocha_api_key = os.getenv("BOCHA_API_KEY", "")
        self.bidding_service = get_bidding_service()
        self.dedup = get_dedup_service()
        self.quality = get_source_quality_service()

        if not self.bocha_api_key:
            logger.warning("BOCHA_API_KEY 环境变量未设置")

    async def _bocha_search(
        self, query: str, count: int = 10, freshness: Optional[str] = None
    ) -> List[Dict]:
        """
        使用 Bocha API 进行搜索

        Args:
            query: 搜索关键词
            count: 返回数量
            freshness: 时间范围（oneDay/oneWeek/oneMonth/oneYear）；
                       None 时用全局 BOCHA_FRESHNESS 配置

        Returns:
            搜索结果列表
        """
        logger.info(f"[_bocha_search] 搜索: query='{query}', count={count}")

        if not self.bocha_api_key:
            logger.error("[_bocha_search] Bocha API key not configured")
            return []

        url = "https://api.bochaai.com/v1/web-search"
        payload = {
            "query": query,
            "summary": True,
            "count": count,
            "page": 1
        }
        freshness_value = freshness if freshness is not None else BOCHA_FRESHNESS
        if freshness_value and freshness_value.lower() != "nolimit":
            payload["freshness"] = freshness_value
        headers = {
            'Authorization': f"Bearer {self.bocha_api_key}",
            'Content-Type': 'application/json'
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                logger.info(f"[_bocha_search] 发送请求到 {url}")
                response = await client.post(url, headers=headers, json=payload)
                logger.info(f"[_bocha_search] 响应状态码: {response.status_code}")
                response.raise_for_status()
                data = response.json()
                logger.info(f"[_bocha_search] 响应数据键: {data.keys() if isinstance(data, dict) else type(data)}")

                webpages_data = data.get('data', {}).get('webPages', {})
                value_list = webpages_data.get('value', [])
                logger.info(f"[_bocha_search] 获取到 {len(value_list) if isinstance(value_list, list) else 0} 条结果")

                if not isinstance(value_list, list):
                    logger.warning(f"[_bocha_search] value_list 不是列表: {type(value_list)}")
                    return []

                results = []
                for item in value_list:
                    if item.get('url') and (item.get('snippet') or item.get('summary')):
                        results.append({
                            'url': item.get('url', ''),
                            'title': item.get('name', ''),
                            'summary': item.get('summary', '') or item.get('snippet', ''),
                            'snippet': item.get('snippet', ''),
                            'siteName': item.get('siteName', ''),
                            'datePublished': item.get('datePublished', ''),
                        })

                logger.info(f"[_bocha_search] 返回 {len(results)} 条有效结果")
                return results

        except Exception as e:
            logger.error(f"[_bocha_search] Bocha search error for '{query}': {e}", exc_info=True)
            return []

    async def collect_news(self, max_items: int = 20, industry_id: Optional[str] = None,
                           focus_user_id=None) -> Dict[str, Any]:
        """
        采集行业资讯

        Args:
            max_items: 最大采集数量
            industry_id: 行业ID，用于获取对应的搜索关键词

        Returns:
            采集结果
        """
        # 获取行业配置
        industry_config = get_industry_config(industry_id)
        news_keywords = list(industry_config.news_keywords)
        if focus_user_id:
            focus_queries = self.db.query(FocusTopic.query_text).filter(
                FocusTopic.user_id == focus_user_id,
                FocusTopic.is_active.is_(True)).limit(20).all()
            news_keywords.extend(query for (query,) in focus_queries if query)
            news_keywords = list(dict.fromkeys(news_keywords))
        logger.info(f"[collect_news] 使用行业: {industry_config.name}, 关键词数量: {len(news_keywords)}")

        task = NewsCollectionTask(
            task_type="news",
            status="running",
            started_at=datetime.utcnow()
        )
        self.db.add(task)
        self.db.commit()

        collected = []
        errors = []
        duplicates = 0
        low_quality_skipped = 0

        try:
            items_per_keyword = max(2, max_items // len(news_keywords))
            # 质量过滤会丢弃相当比例结果（文档站/题库站），故按倍数多取，保证可用条数
            fetch_count = min(max(items_per_keyword * 3, 15), BOCHA_MAX_COUNT)
            logger.info(
                f"[collect_news] 每关键词目标 {items_per_keyword} 条，"
                f"实际请求 {fetch_count} 条（多取少留），freshness={BOCHA_FRESHNESS}"
            )

            for keyword in news_keywords:
                if len(collected) >= max_items:
                    break

                try:
                    # 使用Bocha搜索
                    results = await self._bocha_search(keyword, count=fetch_count)

                    if not results:
                        errors.append(f"搜索 '{keyword}' 无结果")
                        continue

                    count = 0
                    for item in results:
                        if count >= items_per_keyword:
                            break
                        if len(collected) >= max_items:
                            break

                        source_url = item.get('url', '')
                        if not source_url:
                            continue

                        # 来源质量过滤：黑名单域名 / 文档站 / 题库教育站 / 低质聚合
                        # 必须在入库与 LLM 抽取之前拦截，避免污染知识库并浪费算力
                        verdict = self.quality.check(
                            source_url, title=item.get('title', '')
                        )
                        if not verdict.accepted:
                            low_quality_skipped += 1
                            logger.debug(
                                f"[quality] 丢弃低质来源({verdict.reason}): "
                                f"{item.get('title', '')[:40]}"
                            )
                            continue

                        # 检查是否已存在（URL 精确去重）
                        existing = self.db.query(IndustryNews).filter(
                            IndustryNews.source_url == source_url
                        ).first()

                        if existing:
                            continue

                        # 解析发布时间
                        publish_time = self._parse_datetime(item.get('datePublished', ''))
                        if not publish_time:
                            publish_time = self._extract_date_from_snippet(item.get('snippet', ''))

                        # 判断分类
                        title = item.get('title', '')
                        content = item.get('summary', '') or item.get('snippet', '')
                        category = self._categorize_news(title, content)

                        # 内容指纹去重（近重复检测：转载/改写/同事件多源报道）
                        fingerprint = self.dedup.compute_fingerprint(title, content)
                        duplicate_of = self.dedup.find_duplicate(
                            self.db, fingerprint, title=title, content=content
                        )
                        if duplicate_of:
                            duplicates += 1
                            logger.debug(
                                f"[dedup] 跳过近重复资讯: '{title[:50]}' -> 重复于 {duplicate_of}"
                            )
                            continue

                        news = IndustryNews(
                            industry_id=industry_id or DEFAULT_INDUSTRY_ID,
                            title=title[:500] if title else '无标题',
                            content=content,
                            source=item.get('siteName', '') or self._extract_source_from_link(source_url),
                            source_url=source_url,
                            category=category,
                            department=self._extract_department(title, content),
                            publish_time=publish_time,
                            keywords=keyword,
                            content_fingerprint=fingerprint,
                            collected_at=datetime.utcnow()
                        )

                        self.db.add(news)
                        collected.append(news)
                        count += 1

                    # 避免请求过于频繁
                    await asyncio.sleep(0.3)

                except Exception as e:
                    errors.append(f"处理关键词 '{keyword}' 时出错: {str(e)}")
                    logger.error(f"Error processing keyword '{keyword}': {e}")

            self.db.commit()

            task.status = "completed"
            task.total_collected = len(collected)
            task.completed_at = datetime.utcnow()
            if errors:
                task.error_message = "; ".join(errors[:5])
            self.db.commit()

            logger.info(
                f"[collect_news] 采集完成: 新增 {len(collected)} 条, "
                f"来源质量过滤 {low_quality_skipped} 条, 近重复过滤 {duplicates} 条"
            )

            return {
                "success": True,
                "collected": len(collected),
                "duplicates_filtered": duplicates,
                "low_quality_filtered": low_quality_skipped,
                "errors": errors
            }

        except Exception as e:
            task.status = "failed"
            task.error_message = str(e)
            task.completed_at = datetime.utcnow()
            self.db.commit()
            logger.error(f"News collection failed: {e}")

            return {
                "success": False,
                "error": str(e),
                "collected": len(collected)
            }

    async def collect_bidding(self, max_items: int = 20, industry_id: Optional[str] = None) -> Dict[str, Any]:
        """
        采集招投标信息

        Args:
            max_items: 最大采集数量
            industry_id: 行业ID，用于获取对应的搜索关键词

        Returns:
            采集结果
        """
        # 获取行业配置
        industry_config = get_industry_config(industry_id)
        bidding_keywords = industry_config.bidding_keywords
        logger.info(f"[collect_bidding] 使用行业: {industry_config.name}, 关键词数量: {len(bidding_keywords)}")

        task = NewsCollectionTask(
            task_type="bidding",
            status="running",
            started_at=datetime.utcnow()
        )
        self.db.add(task)
        self.db.commit()

        collected = []
        errors = []
        quota_exhausted = False

        try:
            items_per_keyword = max(3, max_items // len(bidding_keywords))

            for keyword in bidding_keywords:
                if len(collected) >= max_items or quota_exhausted:
                    break

                try:
                    # 查询招标信息
                    bid_result = await self.bidding_service.search_bid_notices(
                        keyword=keyword,
                        page=1
                    )

                    # 检查是否配额用尽
                    if bid_result.get("quota_exhausted"):
                        logger.warning(f"[collect_bidding] API 配额已用尽，停止采集")
                        errors.append("招投标 API 配额已用尽，请续费或等待配额重置")
                        quota_exhausted = True
                        break

                    if bid_result.get("success"):
                        for item in bid_result.get("results", [])[:items_per_keyword]:
                            if len(collected) >= max_items:
                                break

                            bid_id = item.get("id")
                            if not bid_id:
                                continue

                            # 检查是否已存在
                            existing = self.db.query(BiddingInfo).filter(
                                BiddingInfo.bid_id == bid_id
                            ).first()

                            if existing:
                                continue

                            # 解析发布时间
                            publish_time = self._parse_datetime(item.get("publish_time"))

                            bidding = BiddingInfo(
                                industry_id=industry_id or DEFAULT_INDUSTRY_ID,
                                bid_id=bid_id,
                                title=item.get("title", "")[:500],
                                notice_type=item.get("notice_type", "招标"),
                                province=item.get("province"),
                                city=item.get("city"),
                                publish_time=publish_time,
                                source=item.get("source", "81api"),
                                collected_at=datetime.utcnow()
                            )

                            self.db.add(bidding)
                            collected.append(bidding)
                    else:
                        errors.append(f"查询招标 '{keyword}' 失败: {bid_result.get('error', '未知错误')}")

                    if quota_exhausted:
                        break

                    # 查询中标信息
                    win_result = await self.bidding_service.search_win_bids(
                        keyword=keyword,
                        page=1
                    )

                    # 检查是否配额用尽
                    if win_result.get("quota_exhausted"):
                        logger.warning(f"[collect_bidding] API 配额已用尽，停止采集")
                        errors.append("招投标 API 配额已用尽，请续费或等待配额重置")
                        quota_exhausted = True
                        break

                    if win_result.get("success"):
                        for item in win_result.get("results", [])[:items_per_keyword]:
                            if len(collected) >= max_items:
                                break

                            bid_id = item.get("id")
                            if not bid_id:
                                continue

                            # 检查是否已存在
                            existing = self.db.query(BiddingInfo).filter(
                                BiddingInfo.bid_id == bid_id
                            ).first()

                            if existing:
                                continue

                            publish_time = self._parse_datetime(item.get("publish_time"))

                            bidding = BiddingInfo(
                                industry_id=industry_id or DEFAULT_INDUSTRY_ID,
                                bid_id=bid_id,
                                title=item.get("title", "")[:500],
                                notice_type=item.get("notice_type", "中标"),
                                province=item.get("province"),
                                city=item.get("city"),
                                publish_time=publish_time,
                                source=item.get("source", "81api"),
                                collected_at=datetime.utcnow()
                            )

                            self.db.add(bidding)
                            collected.append(bidding)

                    if not quota_exhausted:
                        await asyncio.sleep(0.3)

                except Exception as e:
                    errors.append(f"处理关键词 '{keyword}' 时出错: {str(e)}")
                    logger.error(f"Error processing bidding keyword '{keyword}': {e}")

            self.db.commit()

            task.status = "completed"
            task.total_collected = len(collected)
            task.completed_at = datetime.utcnow()
            if errors:
                task.error_message = "; ".join(errors[:5])
            self.db.commit()

            return {
                "success": True,
                "collected": len(collected),
                "errors": errors
            }

        except Exception as e:
            task.status = "failed"
            task.error_message = str(e)
            task.completed_at = datetime.utcnow()
            self.db.commit()
            logger.error(f"Bidding collection failed: {e}")

            return {
                "success": False,
                "error": str(e),
                "collected": len(collected)
            }

    async def collect_all(
        self,
        max_news: int = 20,
        max_bidding: int = 20,
        industry_id: Optional[str] = None,
        focus_user_id=None,
    ) -> Dict[str, Any]:
        """
        采集所有资讯（行业资讯+招投标）

        Args:
            max_news: 最大资讯采集数量
            max_bidding: 最大招投标采集数量
            industry_id: 行业ID
        """
        industry_config = get_industry_config(industry_id)
        logger.info(f"[NewsCollectionService] collect_all 开始: industry={industry_config.name}, max_news={max_news}, max_bidding={max_bidding}")
        logger.info(f"[NewsCollectionService] BOCHA_API_KEY 已配置: {bool(self.bocha_api_key)}")

        news_result = await self.collect_news(max_news, industry_id, focus_user_id)
        logger.info(f"[NewsCollectionService] collect_news 结果: {news_result}")

        # 检查招投标 API 是否可用（占位符 Key 会被 BiddingService 归一化为空）
        if not self.bidding_service.configured:
            skip_reason = (
                "BID_APP_CODE 仍是占位符，需在 backend/.env 填入阿里云市场真实 AppCode"
                if getattr(self.bidding_service, "is_placeholder", False)
                else "BID_APP_CODE 未配置"
            )
            logger.warning(f"[NewsCollectionService] 招投标 API 不可用，跳过采集（{skip_reason}）")
            bidding_result = {
                "success": True,
                "collected": 0,
                "errors": [],
                "skipped": skip_reason,
            }
        else:
            bidding_result = await self.collect_bidding(max_bidding, industry_id)
            logger.info(f"[NewsCollectionService] collect_bidding 结果: {bidding_result}")

        result = {
            "success": news_result.get("success") or bidding_result.get("success"),
            "news": news_result,
            "bidding": bidding_result,
            "industry": industry_config.name
        }
        logger.info(f"[NewsCollectionService] collect_all 返回: {result}")
        return result

    # ---------- 历史低质数据清理 ----------

    def scan_low_quality_news(self, industry_id: Optional[str] = None) -> Dict[str, Any]:
        """扫描已入库资讯中命中质量过滤规则的记录（只读，不删除）

        质量过滤器是后加的，历史数据里仍混有豆丁网/道客巴巴/题库站等低质来源，
        需要一次性清理。本方法只统计，删除请用 clean_low_quality_news。

        Returns:
            {"total": 总条数, "low_quality": 命中条数, "by_domain": {...}, "samples": [...]}
        """
        query = self.db.query(IndustryNews)
        if industry_id:
            query = query.filter(IndustryNews.industry_id == industry_id)

        total = 0
        hits = []
        by_domain: Dict[str, int] = {}
        by_reason: Dict[str, int] = {}

        for news in query.yield_per(500):
            total += 1
            verdict = self.quality.check(news.source_url or "", title=news.title or "")
            if verdict.accepted:
                continue
            hits.append(news)
            by_domain[verdict.host or "unknown"] = by_domain.get(verdict.host or "unknown", 0) + 1
            key = verdict.reason.split("(")[0]
            by_reason[key] = by_reason.get(key, 0) + 1

        samples = [
            {"id": n.id, "title": (n.title or "")[:60], "url": n.source_url}
            for n in hits[:10]
        ]
        return {
            "total": total,
            "low_quality": len(hits),
            "by_domain": dict(sorted(by_domain.items(), key=lambda kv: -kv[1])),
            "by_reason": by_reason,
            "samples": samples,
        }

    def clean_low_quality_news(
        self, industry_id: Optional[str] = None, dry_run: bool = True
    ) -> Dict[str, Any]:
        """删除已入库的低质来源资讯

        Args:
            industry_id: 限定行业；None 表示全部行业
            dry_run: True 只统计不删除（默认）

        Returns:
            统计结果，dry_run=True 时含 samples
        """
        scan = self.scan_low_quality_news(industry_id)
        result = {
            "success": True,
            "dry_run": dry_run,
            "total": scan["total"],
            "low_quality": scan["low_quality"],
            "by_domain": scan["by_domain"],
            "by_reason": scan["by_reason"],
            "deleted": 0,
        }

        if dry_run:
            result["samples"] = scan["samples"]
            logger.info(
                f"[clean_low_quality] DRY-RUN: 共 {scan['total']} 条，"
                f"命中低质 {scan['low_quality']} 条（未删除）"
            )
            return result

        query = self.db.query(IndustryNews)
        if industry_id:
            query = query.filter(IndustryNews.industry_id == industry_id)

        deleted = 0
        for news in query.yield_per(500):
            if self.quality.check(news.source_url or "", title=news.title or "").accepted:
                continue
            self.db.delete(news)
            deleted += 1

        self.db.commit()
        result["deleted"] = deleted
        logger.info(
            f"[clean_low_quality] 已删除低质资讯 {deleted} 条"
            f"（行业={industry_id or '全部'}）"
        )
        return result

    def get_news_list(
        self,
        category: Optional[str] = None,
        industry_id: Optional[str] = None,
        event_type: Optional[str] = None,
        impact_direction: Optional[str] = None,
        user_id=None,
        limit: int = 20,
        offset: int = 0
    ) -> tuple[List[Dict], int]:
        """获取资讯列表，返回 (items, filtered_total)"""
        query = self.db.query(IndustryNews).order_by(IndustryNews.collected_at.desc())

        # 按行业筛选
        if industry_id:
            query = query.filter(IndustryNews.industry_id == industry_id)

        if category:
            query = query.filter(IndustryNews.category == category)

        # 按 LLM 抽取的事件类型筛选（化工资讯核心维度）
        if event_type:
            query = query.filter(IndustryNews.event_type == event_type)

        # 按影响方向筛选：positive/negative/neutral
        if impact_direction:
            query = query.filter(IndustryNews.impact_direction == impact_direction)

        # 个人屏蔽：只排除「当前用户」屏蔽过的资讯。
        # industry_news 是共享语料，所以这里刻意用「当前用户 + 子查询」而不是删除数据，
        # 保证共享库、其他用户视图、向量索引都不受影响。详见 service/news_hidden_service.py。
        if user_id:
            hidden_sub = (
                self.db.query(NewsHidden.news_id)
                .filter(NewsHidden.user_id == user_id)
                .scalar_subquery()
            )
            query = query.filter(~IndustryNews.id.in_(hidden_sub))

        # 先获取筛选后的总数
        filtered_total = query.count()

        if user_id:
            from service.feedback_learning_service import FeedbackLearningService
            ranked = FeedbackLearningService(self.db).enrich_and_rank(query.all(), user_id)
            return ranked[offset:offset + limit], filtered_total

        items = query.offset(offset).limit(limit).all()
        return [item.to_dict() for item in items], filtered_total

    def get_bidding_list(
        self,
        notice_type: Optional[str] = None,
        province: Optional[str] = None,
        industry_id: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> tuple[List[Dict], int]:
        """获取招投标列表，返回 (items, filtered_total)"""
        from sqlalchemy import or_

        query = self.db.query(BiddingInfo).order_by(BiddingInfo.collected_at.desc())

        # 按行业筛选
        if industry_id:
            query = query.filter(BiddingInfo.industry_id == industry_id)

        if notice_type:
            # 支持模糊匹配：如 "招标" 匹配 "招标公告"、"采购公告" 等
            if notice_type == "招标":
                query = query.filter(
                    or_(
                        BiddingInfo.notice_type.like("%招标%"),
                        BiddingInfo.notice_type.like("%采购%"),
                        BiddingInfo.notice_type.like("%询价%")
                    )
                )
            elif notice_type == "中标":
                query = query.filter(
                    or_(
                        BiddingInfo.notice_type.like("%中标%"),
                        BiddingInfo.notice_type.like("%结果%")
                    )
                )
            else:
                query = query.filter(BiddingInfo.notice_type == notice_type)
        if province:
            query = query.filter(BiddingInfo.province == province)

        # 先获取筛选后的总数
        filtered_total = query.count()

        items = query.offset(offset).limit(limit).all()
        return [item.to_dict() for item in items], filtered_total

    def get_news_stats(self, industry_id: Optional[str] = None) -> Dict[str, Any]:
        """获取资讯统计"""
        from sqlalchemy import func

        query = self.db.query(IndustryNews)
        if industry_id:
            query = query.filter(IndustryNews.industry_id == industry_id)

        total = query.count()

        # 按分类统计
        cat_query = self.db.query(
            IndustryNews.category,
            func.count(IndustryNews.id).label("count")
        )
        if industry_id:
            cat_query = cat_query.filter(IndustryNews.industry_id == industry_id)
        category_stats = cat_query.group_by(IndustryNews.category).all()

        # 获取最近24小时更新数
        yesterday = datetime.utcnow() - timedelta(hours=24)
        recent_query = self.db.query(IndustryNews).filter(
            IndustryNews.collected_at >= yesterday
        )
        if industry_id:
            recent_query = recent_query.filter(IndustryNews.industry_id == industry_id)
        recent_count = recent_query.count()

        # 按事件类型统计（LLM 抽取结果）
        event_query = self.db.query(
            IndustryNews.event_type,
            func.count(IndustryNews.id).label("count")
        )
        if industry_id:
            event_query = event_query.filter(IndustryNews.industry_id == industry_id)
        event_stats = event_query.group_by(IndustryNews.event_type).all()

        return {
            "total": total,
            "recent_24h": recent_count,
            "by_category": {cat: cnt for cat, cnt in category_stats if cat},
            "by_event_type": {evt: cnt for evt, cnt in event_stats if evt}
        }

    def get_bidding_stats(self, industry_id: Optional[str] = None) -> Dict[str, Any]:
        """获取招投标统计"""
        from sqlalchemy import func

        query = self.db.query(BiddingInfo)
        if industry_id:
            query = query.filter(BiddingInfo.industry_id == industry_id)

        total = query.count()

        # 按类型统计（原始类型）
        type_query = self.db.query(
            BiddingInfo.notice_type,
            func.count(BiddingInfo.id).label("count")
        )
        if industry_id:
            type_query = type_query.filter(BiddingInfo.industry_id == industry_id)
        type_stats = type_query.group_by(BiddingInfo.notice_type).all()

        # 归类统计：中标类 vs 招标类
        bid_count = 0  # 招标
        win_count = 0  # 中标
        raw_by_type = {}
        for notice_type, count in type_stats:
            if notice_type:
                raw_by_type[notice_type] = count
                # 归类
                if "中标" in notice_type or "结果" in notice_type:
                    win_count += count
                elif "招标" in notice_type or "采购" in notice_type or "询价" in notice_type:
                    bid_count += count

        # 按省份统计（前10）
        province_query = self.db.query(
            BiddingInfo.province,
            func.count(BiddingInfo.id).label("count")
        )
        if industry_id:
            province_query = province_query.filter(BiddingInfo.industry_id == industry_id)
        province_stats = province_query.group_by(BiddingInfo.province).order_by(
            func.count(BiddingInfo.id).desc()
        ).limit(10).all()

        return {
            "total": total,
            "by_type": {
                "招标": bid_count,
                "中标": win_count,
                **raw_by_type  # 保留原始类型以便调试
            },
            "by_province": {p: c for p, c in province_stats if p}
        }

    def has_data(self) -> bool:
        """检查是否有数据"""
        news_count = self.db.query(IndustryNews).count()
        bidding_count = self.db.query(BiddingInfo).count()
        return news_count > 0 or bidding_count > 0

    def _extract_date_from_snippet(self, snippet: str) -> Optional[datetime]:
        """从snippet中提取日期"""
        import re

        if not snippet:
            return None

        patterns = [
            r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})[日号]?',
            r'(\d{4})\.(\d{1,2})\.(\d{1,2})',
        ]

        for pattern in patterns:
            match = re.search(pattern, snippet)
            if match:
                try:
                    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
                    if 2020 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31:
                        return datetime(year, month, day)
                except:
                    pass

        return None

    def _parse_datetime(self, date_str: str) -> Optional[datetime]:
        """解析日期时间字符串"""
        if not date_str:
            return None

        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
            "%Y年%m月%d日",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str.split('.')[0].split('+')[0], fmt)
            except:
                pass

        return None

    def _categorize_news(self, title: str, content: str) -> str:
        """判断资讯分类"""
        text = f"{title} {content}".lower()

        if any(kw in text for kw in ["政策", "通知", "意见", "办法", "规定", "条例", "规划", "法规"]):
            return "政策"
        if any(kw in text for kw in ["纪要", "会议", "座谈", "研讨"]):
            return "纪要"
        if any(kw in text for kw in ["研报", "研究报告", "分析报告", "白皮书", "行业报告"]):
            return "研报"

        return "新闻"

    def _extract_source_from_link(self, link: str) -> str:
        """从链接提取来源"""
        if not link:
            return "未知来源"

        try:
            from urllib.parse import urlparse
            parsed = urlparse(link)
            domain = parsed.netloc

            source_map = {
                "gov.cn": "政府网站",
                "xinhuanet.com": "新华网",
                "people.com.cn": "人民网",
                "mot.gov.cn": "交通运输部",
                "ndrc.gov.cn": "国家发改委",
                "miit.gov.cn": "工信部",
                "163.com": "网易",
                "sohu.com": "搜狐",
                "sina.com": "新浪",
                "qq.com": "腾讯",
                "baidu.com": "百度",
            }

            for key, name in source_map.items():
                if key in domain:
                    return name

            # 返回主域名
            parts = domain.split('.')
            if len(parts) >= 2:
                return '.'.join(parts[-2:])
            return domain
        except:
            return "未知来源"

    def _extract_department(self, title: str, content: str) -> Optional[str]:
        """提取发布部门"""
        text = f"{title} {content}"

        departments = [
            "国务院", "交通运输部", "工信部", "发改委", "科技部",
            "住建部", "公安部", "财政部", "自然资源部", "工业和信息化部",
            "国家发展改革委", "交通运输厅",
        ]

        for dept in departments:
            if dept in text:
                return dept

        return None


def get_news_collection_service(db: Session) -> NewsCollectionService:
    """获取服务实例"""
    return NewsCollectionService(db)
