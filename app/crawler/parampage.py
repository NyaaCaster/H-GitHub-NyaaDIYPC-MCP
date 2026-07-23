"""
深参页（param.shtml）解析 — 第二层数据补全。

列表内嵌参数为精简截断版，兼容关键字（散热器适用范围、SSD 外形尺寸、
机箱 USB 接口等）需要从深参页补全。

解析规则取自 01-爬虫模块详细设计 §4。
"""

import asyncio
import logging
import os
import re
from typing import Optional

from app.crawler.fetch import CrawlSession

logger = logging.getLogger(__name__)

# 深参页 Referer
DEEP_REFERER = "https://detail.zol.com.cn/"

# 深参页表格解析正则
RE_PARAM_NAME = re.compile(r'id="newPmName_(\d+)"[^>]*>(.*?)</span>')
RE_PARAM_VAL = re.compile(r'id="newPmVal_(\d+)"[^>]*>(.*?)</span>')

# HTML 标签清理
RE_HTML_TAG = re.compile(r"<[^>]+>")


def _clean(value: str) -> str:
    """清理 HTML 标签：<br> → /，去其他标签，去首尾空白。"""
    cleaned = RE_HTML_TAG.sub("", value.replace("<br>", "/").replace("<br/>", "/"))
    return cleaned.strip()


def parse_deep_html(html: str) -> dict[str, str]:
    """从 param.shtml 页 HTML 解析完整参数表。

    Returns:
        {参数名: 值} 字典。
    """
    names: dict[str, str] = {}
    for m in RE_PARAM_NAME.finditer(html):
        idx = m.group(1)
        names[idx] = m.group(2).strip()

    vals: dict[str, str] = {}
    for m in RE_PARAM_VAL.finditer(html):
        idx = m.group(1)
        raw = m.group(2)
        vals[idx] = _clean(raw)

    result: dict[str, str] = {}
    for idx, name in names.items():
        if idx in vals and vals[idx]:
            result[name] = vals[idx]

    return result


async def fetch_deep_params(
    session: CrawlSession,
    morep_url: str,
) -> dict[str, str]:
    """抓取一个型号的深参页，返回完整参数字典。

    Args:
        session: CrawlSession 实例。
        morep_url: 从 GetGoods 条目 .morep href 取得的路径
                   （如 //detail.zol.com.cn/1404/1403088/param.shtml）。

    Returns:
        {参数名: 值} 字字典。抓取失败返回空 dict。
    """
    # 构造完整 URL
    if morep_url.startswith("//"):
        url = "https:" + morep_url
    elif morep_url.startswith("/"):
        url = "https://detail.zol.com.cn" + morep_url
    else:
        url = morep_url

    try:
        html = await session.fetch(url, referer=DEEP_REFERER)
        params = parse_deep_html(html)
        logger.debug("Deep params fetched: %s → %d fields", url[:80], len(params))
        return params
    except Exception as exc:
        logger.warning("Deep param fetch failed for %s: %s", url[:80], exc)
        return {}


def filter_top_n(
    items: list[dict],
    n: int,
    need_deep: bool = True,
) -> list[dict]:
    """按 popularity 降序取前 N 个条目。

    Args:
        items: 条目列表（含 popularity/int 字段）。
        n: 取前 N 个。
        need_deep: True 则只取 morep_url 非空的条目。

    Returns:
        筛选后的条目列表（引用原 dict，长度 ≤ n）。
    """
    candidates = items
    if need_deep:
        candidates = [it for it in items if it.get("morep_url")]
    # 按 popularity 降序
    candidates = sorted(candidates, key=lambda x: x.get("popularity", 0), reverse=True)
    return candidates[:n]


async def fetch_deep_batch(
    session: CrawlSession,
    items: list[dict],
    top_n: Optional[int] = None,
) -> list[dict]:
    """为一组条目的 Top-N 抓取深参（并发控制由 CrawlSession 内部限速保证）。

    每个条目原地添加 `deep_params` 字段；若抓取失败则 deep_params={}。
    """
    if top_n is None:
        top_n = int(os.getenv("DEEP_TOP_N", "300"))

    # 需抓深参的条目（有 morep_url 且 popularity > 0）
    candidates = [it for it in items if it.get("morep_url")]
    if not candidates:
        logger.info("No items with morep_url to deep-fetch")
        return items

    targets = filter_top_n(candidates, top_n, need_deep=True)
    if not targets:
        return items

    logger.info("Deep-fetching %d/%d items (top_n=%d)", len(targets), len(candidates), top_n)

    # 构造抓取标志集（按 pro_id）
    target_ids = {it["pro_id"] for it in targets}

    # 逐个串行抓取（并发由 CrawlSession.rate_limit 内部 semaphore 控制，
    # 但这里我们显式串行以确保礼貌）
    for i, item in enumerate(targets, start=1):
        morep = item.get("morep_url", "")
        try:
            deep_params = await fetch_deep_params(session, morep)
        except Exception as exc:
            logger.warning("[%d/%d] Deep fetch failed for %s: %s", i, len(targets), morep[:80], exc)
            deep_params = {}
        item["deep_params"] = deep_params

        if i % 10 == 0 or i == len(targets):
            logger.info("Deep params: %d/%d done", i, len(targets))

    # 未抓深参的条目也初始化空字段
    for item in items:
        if "deep_params" not in item:
            item["deep_params"] = {}

    return items
