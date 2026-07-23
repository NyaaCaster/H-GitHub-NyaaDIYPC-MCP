"""
CPU / 显卡天梯图解析。

数据源：
  CPU: https://cpu.zol.com.cn/router.php?c=Tianti_Cpu&a=GetList (JSON, GBK)
      取 game 维度（装机场景优先），colligate 作备用。
  GPU: https://vga.zol.com.cn/soc/ (HTML, GBK)
      解析 .item-box[data-proid] + .num(score)

解析规则取自 01-爬虫模块详细设计 §5。
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from app.crawler.fetch import CrawlSession

logger = logging.getLogger(__name__)

# ---- 接口 URL ----
CPU_TIANTI_URL = "https://cpu.zol.com.cn/router.php?c=Tianti_Cpu&a=GetList"
CPU_TIANTI_REFERER = "https://cpu.zol.com.cn/soc/"

GPU_TIANTI_URL = "https://vga.zol.com.cn/soc/"
GPU_TIANTI_REFERER = "https://vga.zol.com.cn/"

# ---- GPU HTML 解析 ----
RE_ITEM_BOX = re.compile(
    r'<div\s+class="item-box\s+clearfix"[^>]*\bdata-proid="(\d+)"[^>]*>'
)
RE_SCORE_NUM = re.compile(r'<div\s+class="num">(\d+)</div>')
RE_MODEL_DT = re.compile(r"<dt[^>]*>([^<]+)</dt>")


def _parse_cpu_tier_item(dimension: str, item: dict) -> dict:
    """解析单条 CPU 天梯 JSON 条目。

    Args:
        dimension: 维度名（game/colligate/singleCore/multiCore）。
        item: {"firm":"1|2","proId":"2118101","model":"AMD Ryzen 9 9950X3D",
               "score":"409.0","rank":1,"ratio":"99.3%"}

    Returns:
        标准化 tier dict。
    """
    return {
        "pro_id": item.get("proId", ""),
        "kind": "cpu",
        "dimension": dimension,
        "model": item.get("model", ""),
        "score": float(item.get("score", 0)),
        "rank": int(item.get("rank", 0)),
        "ratio": item.get("ratio", ""),
        "firm": item.get("firm", ""),
    }


async def fetch_cpu_tiers(session: CrawlSession) -> list[dict]:
    """抓取 CPU 天梯数据，取 game 维度为主、colligate 为备用。

    Returns:
        标准化 tier dict 列表（包含 game + colligate 两个维度）。
    """
    data = await session.fetch_json(CPU_TIANTI_URL, referer=CPU_TIANTI_REFERER)
    tiers: list[dict] = []

    # 优先取 game 维度（装机场景最相关）
    for dim_name in ("game", "colligate"):
        items = data.get(dim_name, [])
        if items:
            tiers.extend(_parse_cpu_tier_item(dim_name, it) for it in items)

    logger.info("CPU tiers fetched: %d entries (%d game + %d colligate)",
        len(tiers),
        sum(1 for t in tiers if t["dimension"] == "game"),
        sum(1 for t in tiers if t["dimension"] == "colligate"),
    )
    return tiers


def _parse_gpu_html(html: str) -> list[dict]:
    """从显卡天梯 HTML 页解析条目列表。

    Returns:
        [{"pro_id": str, "kind": "gpu", "dimension": "default",
          "model": str, "score": float, "rank": int}, ...]
    """
    tiers: list[dict] = []
    rank = 0
    for m_box in RE_ITEM_BOX.finditer(html):
        pro_id = m_box.group(1)
        rank += 1
        # 在 item-box 范围内找 score 和 model
        box_html = html[m_box.start():m_box.end() + 2000]  # 取足够长的片段
        # score
        m_score = RE_SCORE_NUM.search(box_html)
        score = float(m_score.group(1)) if m_score else 0.0
        # model
        m_model = RE_MODEL_DT.search(box_html)
        model = m_model.group(1).strip() if m_model else ""

        tiers.append({
            "pro_id": pro_id,
            "kind": "gpu",
            "dimension": "default",
            "model": model,
            "score": score,
            "rank": rank,
            "ratio": "",
            "firm": "",
        })

    logger.info("GPU tiers parsed: %d entries", len(tiers))
    return tiers


async def fetch_gpu_tiers(session: CrawlSession) -> list[dict]:
    """抓取显卡天梯 HTML 页并解析。

    Returns:
        标准化 tier dict 列表。
    """
    html = await session.fetch(GPU_TIANTI_URL, referer=GPU_TIANTI_REFERER)
    return _parse_gpu_html(html)


async def fetch_all_tiers(
    session: CrawlSession,
) -> dict[str, list[dict]]:
    """抓取全部天梯数据（CPU + GPU）。

    Returns:
        {"cpu": [...], "gpu": [...]}
    """
    tiers: dict[str, list[dict]] = {}

    try:
        tiers["cpu"] = await fetch_cpu_tiers(session)
    except Exception as exc:
        logger.error("CPU tier fetch failed: %s", exc)
        tiers["cpu"] = []

    try:
        tiers["gpu"] = await fetch_gpu_tiers(session)
    except Exception as exc:
        logger.error("GPU tier fetch failed: %s", exc)
        tiers["gpu"] = []

    return tiers
