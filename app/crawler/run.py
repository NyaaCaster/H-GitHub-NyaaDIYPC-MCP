"""
爬虫编排与入口 — 全量/按品类抓取 → 深参补全 → 归一 → 落库。

触发方式（审计整改 1：爬虫不入 MCP 工具面）：
  - cron: 容器内定时任务调 `python -m app.crawler.run --category all`
  - 手动: `python -m app.crawler.run --category cpu`
  - Python API: `asyncio.run(run_crawl(db_path, categories=[...]))`
"""

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

from app.crawler import CATEGORIES, CATEGORY_NAMES, TIER_CATEGORIES, get_subcate_id
from app.crawler.fetch import CrawlSession
from app.crawler.getgoods import fetch_category
from app.crawler.parampage import fetch_deep_batch
from app.crawler.normalize import normalize_compat
from app.crawler.store import (
    backup_db,
    mark_inactive,
    record_meta,
    store_category,
    upsert_tiers,
)
from app.crawler.tianti import fetch_all_tiers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("crawler.run")


def _resolve_db_path() -> str:
    """从环境变量获取 DB 路径，确保父目录存在。"""
    db_path = os.getenv("DIYPC_DB_PATH", "/app/data/diypc.db")
    parent = os.path.dirname(db_path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
        logger.info("Created DB directory: %s", parent)
    return db_path


async def run_crawl(
    db_path: str,
    categories: list[str] | None = None,
) -> dict:
    """执行一次全量（或部分品类）抓取。

    Args:
        db_path: SQLite 数据库路径。
        categories: 要抓取的品类名列表；None = 全部 9 类。

    Returns:
        {"categories": {cat: {"items": int, "compat": int, "tiers": int}}, ...}
    """
    if categories is None:
        categories = CATEGORY_NAMES

    # 验证品类名
    for cat in categories:
        if cat not in CATEGORIES:
            raise ValueError(f"Unknown category: {cat}")

    logger.info("Crawl starting: categories=%s, db=%s", categories, db_path)

    # 备份
    backup_db(db_path)

    # 连接 DB
    conn = sqlite3.connect(db_path)

    stats: dict = {}

    try:
        async with CrawlSession() as session:
            # ---- 天梯（CPU + GPU，优先抓取以供后续 tier join） ----
            tier_needed = [c for c in categories if c in TIER_CATEGORIES]
            all_tiers: dict[str, list[dict]] = {}
            if tier_needed:
                logger.info("Fetching tiers for: %s", tier_needed)
                all_tiers = await fetch_all_tiers(session)

            # ---- 逐品类抓取 ----
            for category in categories:
                cat_start = time.monotonic()
                logger.info("=== Category: %s ===", category)

                subcate_id = get_subcate_id(category)
                if subcate_id < 0:
                    logger.error("No subcate_id for category=%s, skip", category)
                    continue

                # 1) 抓取 GetGoods 全量
                items = await fetch_category(session, subcate_id)
                if not items:
                    logger.warning("No items fetched for category=%s", category)
                    stats[category] = {"items": 0, "compat": 0, "tiers": 0}
                    continue

                logger.info("%s: %d items from GetGoods", category, len(items))

                # 2) 深参补全（Top-N）
                deep_n = int(os.getenv("DEEP_TOP_N", "300"))
                items = await fetch_deep_batch(session, items, top_n=deep_n)

                # 3) 归一 → compat
                for item in items:
                    embed = item.get("embed_params", {})
                    deep = item.get("deep_params", {})
                    item["compat"] = normalize_compat(category, embed, deep)

                # 4) 天梯数据
                tiers = all_tiers.get(category, [])

                # 5) 落库
                store_category(conn, category, items, tiers, db_path=db_path)

                # 6) 回填 tier_score 到 hardware
                if tiers:
                    _backfill_tier_scores(conn, category, tiers)
                    conn.commit()

                elapsed = time.monotonic() - cat_start
                stats[category] = {
                    "items": len(items),
                    "compat": sum(1 for it in items if it.get("compat")),
                    "tiers": len(tiers),
                    "elapsed_s": round(elapsed, 1),
                }
                logger.info(
                    "%s done: %d items, %d compat, %d tiers (%.1fs)",
                    category, stats[category]["items"],
                    stats[category]["compat"], stats[category]["tiers"],
                    elapsed,
                )

        # 写入全局 meta
        now = datetime.now(timezone.utc).isoformat()
        record_meta(conn, "last_crawl_at", now)
        conn.commit()

    except Exception:
        conn.rollback()
        logger.exception("Crawl failed — changes rolled back")
        raise
    finally:
        conn.close()

    return stats


def _backfill_tier_scores(
    conn: sqlite3.Connection,
    category: str,
    tiers: list[dict],
) -> None:
    """将天梯 score 回填到 hardware.tier_score。

    CPU 取 game 维度，GPU 取 default 维度。
    """
    kind = "cpu" if category == "cpu" else "gpu"
    preferred_dim = "game" if kind == "cpu" else "default"

    # 按 pro_id 聚合：优先取 preferred_dim 的分数
    score_map: dict[str, float] = {}
    for t in tiers:
        if t.get("kind") != kind:
            continue
        pid = t.get("pro_id", "")
        dim = t.get("dimension", "")
        score = t.get("score", 0)
        if pid not in score_map or dim == preferred_dim:
            score_map[pid] = score

    if not score_map:
        return

    # 批量更新
    rows = [(score, pid) for pid, score in score_map.items()]
    conn.executemany(
        "UPDATE hardware SET tier_score = ? WHERE pro_id = ? AND category = ?",
        [(s, pid, category) for s, pid in rows],
    )
    logger.info(
        "Backfilled tier_score for %d %s items", len(rows), category,
    )


# ============================================================
# CLI 入口
# ============================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NyaaDIYPC-MCP 爬虫 — ZOL 9 品类硬件数据抓取",
    )
    parser.add_argument(
        "--category",
        default="all",
        help="品类名或 'all'（默认 all）。可用: %s" % ", ".join(CATEGORY_NAMES),
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="SQLite 数据库路径（默认 $DIYPC_DB_PATH 或 /app/data/diypc.db）",
    )
    args = parser.parse_args()

    db_path = args.db_path or _resolve_db_path()

    if args.category == "all":
        categories = CATEGORY_NAMES
    else:
        categories = [c.strip() for c in args.category.split(",")]

    # 确保 schema 已初始化
    from app.db.schema import init_db
    init_db(db_path)

    start = time.monotonic()
    stats = asyncio.run(run_crawl(db_path, categories))
    total_s = time.monotonic() - start

    # 汇总
    total_items = sum(s.get("items", 0) for s in stats.values())
    total_compat = sum(s.get("compat", 0) for s in stats.values())
    total_tiers = sum(s.get("tiers", 0) for s in stats.values())
    logger.info(
        "Crawl complete: %d categories, %d items, %d compat, %d tiers (total %.1fs)",
        len(stats), total_items, total_compat, total_tiers, total_s,
    )


if __name__ == "__main__":
    main()
