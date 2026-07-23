"""
GetGoods 接口调用 + 列表条目 HTML 解析。

API: zj.zol.com.cn/index.php?c=Ajax_ParamResponse&a=GetGoods&subcateId={ID}&type=1&page={N}
返回 JSON（GBK 编码），data 字段为商品条目 HTML 片段。

解析规则取自 01-爬虫模块详细设计 §3 和 .ref/ZOL爬虫方案 §1。
"""

import logging
import re
from typing import Optional

from app.crawler.fetch import CrawlSession

logger = logging.getLogger(__name__)

# GetGoods API 模板
GETGOODS_URL = (
    "https://zj.zol.com.cn/index.php"
    "?c=Ajax_ParamResponse&a=GetGoods&subcateId={subcate_id}&type=1&page={page}"
)
GETGOODS_REFERER = "https://zj.zol.com.cn/"

# ---- 条目解析正则（预编译） ----
RE_PRO_ID = re.compile(r'p_(\d+)')
RE_MANU_ID = re.compile(r'relmanu="(\d+)"')
RE_MODEL_TITLE = re.compile(r'<h3><a\s[^>]*\btitle="([^"]*)"')
RE_POPULARITY = re.compile(r"本月<i>(\d+)</i>人已选用")
RE_SPAN_PARAM = re.compile(r"<span[^>]*>([^：<]+)：<em>(.*?)</em></span>")
RE_PRICE = re.compile(r"￥\s*([\d,]+)")
RE_MOREP_HREF = re.compile(r'<a\s[^>]*\bclass="morep"[^>]*\bhref="([^"]*)"')
RE_DETAIL_HREF = re.compile(r'<h3><a\s[^>]*\bhref="([^"]*)"')
RE_PRICE_SHOW = re.compile(r'class="price-box[^"]*"[^>]*>.*?<span[^>]*class="price"[^>]*>([^<]*)', re.S)
RE_PRICE_MIN = re.compile(r'class="sjtotal"[^>]*>([^<]*)', re.S)
RE_PRICE_JD = re.compile(r'class="jd"[^>]*>([^<]*)', re.S)

# 条目边界
RE_PITEM = re.compile(r'<div\s+class="pitem\s+clearfix[^"]*"[^>]*>', re.S)


def _extract_price(text: str) -> Optional[int]:
    """从文本中提取价格数字（去逗号，转 int）。无价格返回 None。"""
    m = RE_PRICE.search(text)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def _parse_prices(item_html: str) -> dict[str, Optional[int]]:
    """从 pitem HTML 片段提取三价格。

    Returns:
        {"price_show": int|None, "price_min": int|None, "price_jd": int|None}
    """
    prices: dict[str, Optional[int]] = {
        "price_show": None,
        "price_min": None,
        "price_jd": None,
    }

    # 主展示价：.price-box .price
    m_show = RE_PRICE_SHOW.search(item_html)
    if m_show:
        prices["price_show"] = _extract_price(m_show.group(1))

    # 多商家最低价：.sjtotal
    m_min = RE_PRICE_MIN.search(item_html)
    if m_min:
        prices["price_min"] = _extract_price(m_min.group(1))

    # 京东价：.jd
    m_jd = RE_PRICE_JD.search(item_html)
    if m_jd:
        prices["price_jd"] = _extract_price(m_jd.group(1))

    return prices


def _parse_embed_params(item_html: str) -> dict[str, str]:
    """从 .paramet 区域提取内嵌参数字典。

    匹配模式：<span ...>标签名：<em>值</em></span>
    """
    params: dict[str, str] = {}
    for match in RE_SPAN_PARAM.finditer(item_html):
        key = match.group(1).strip()
        val = match.group(2).strip()
        params[key] = val
    return params


def parse_item_html(item_html: str) -> Optional[dict]:
    """解析单个 pitem HTML 为结构化 dict。

    Returns:
        {
            "pro_id": str, "manu_id": str, "model": str,
            "popularity": int, "prices": {...}, "embed_params": {...},
            "morep_url": str|None, "detail_url": str|None
        }
        解析失败返回 None。
    """
    # pro_id — 从 class p_{id} 提取
    m_pro = RE_PRO_ID.search(item_html)
    if not m_pro:
        logger.debug("pitem without pro_id, skip")
        return None
    pro_id = m_pro.group(1)

    # manu_id
    m_manu = RE_MANU_ID.search(item_html)
    manu_id = m_manu.group(1) if m_manu else ""

    # 型号名 — h3 a title
    m_model = RE_MODEL_TITLE.search(item_html)
    model = m_model.group(1).strip() if m_model else ""

    # 热度
    m_pop = RE_POPULARITY.search(item_html)
    popularity = int(m_pop.group(1)) if m_pop else 0

    # 价格
    prices = _parse_prices(item_html)

    # 内嵌参数
    embed_params = _parse_embed_params(item_html)

    # 深参链接
    m_deep = RE_MOREP_HREF.search(item_html)
    morep_url = m_deep.group(1) if m_deep else None

    # 详情链接
    m_detail = RE_DETAIL_HREF.search(item_html)
    detail_url = m_detail.group(1) if m_detail else None

    return {
        "pro_id": pro_id,
        "manu_id": manu_id,
        "model": model,
        "popularity": popularity,
        "prices": prices,
        "embed_params": embed_params,
        "morep_url": morep_url,
        "detail_url": detail_url,
    }


def _split_pitem_blocks(html: str) -> list[str]:
    """把 GetGoods data HTML 按 pitem 边界拆分成独立条目块。

    策略：找到每个 <div class="pitem clearfix ..."> 的起始位置，
    下一个 pitem 开始之前（或字符串结尾）即为该条目 HTML 片段。
    """
    blocks: list[str] = []
    positions = [m.start() for m in RE_PITEM.finditer(html)]
    if not positions:
        return blocks

    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(html)
        blocks.append(html[start:end])

    return blocks


async def fetch_page(
    session: CrawlSession,
    subcate_id: int,
    page: int = 1,
) -> dict:
    """抓取 GetGoods 单页，返回解析结果。

    Returns:
        {"items": list[dict], "page": int, "max_page": int, "total": int}
    """
    url = GETGOODS_URL.format(subcate_id=subcate_id, page=page)
    data = await session.fetch_json(url, referer=GETGOODS_REFERER)

    max_page = int(data.get("maxPage", "1"))
    total = int(data.get("allNum", "0"))
    raw_html = data.get("data", "")

    # 解析条目
    blocks = _split_pitem_blocks(raw_html)
    items = []
    for block in blocks:
        item = parse_item_html(block)
        if item:
            items.append(item)

    logger.debug(
        "GetGoods subcate=%d page=%d: %d items parsed (maxPage=%d, allNum=%d)",
        subcate_id, page, len(items), max_page, total,
    )

    return {"items": items, "page": page, "max_page": max_page, "total": total}


async def fetch_category(
    session: CrawlSession,
    subcate_id: int,
    max_pages: int = 0,
) -> list[dict]:
    """抓取一个品类的全部页，返回所有条目列表。

    Args:
        session: CrawlSession 实例。
        subcate_id: ZOL 品类 ID。
        max_pages: 最多抓取页数（0=全部）。

    Returns:
        该品类所有条目 dict 列表。
    """
    # 先抓第 1 页获取分页信息
    first = await fetch_page(session, subcate_id, page=1)
    all_items: list[dict] = list(first["items"])

    total_pages = first["max_page"]
    if max_pages and max_pages < total_pages:
        total_pages = max_pages

    if total_pages <= 1:
        return all_items

    logger.info(
        "Fetching category subcate=%d: pages 2..%d (%d items so far)",
        subcate_id, total_pages, len(all_items),
    )

    for page in range(2, total_pages + 1):
        try:
            result = await fetch_page(session, subcate_id, page=page)
            all_items.extend(result["items"])
        except Exception as exc:
            logger.error(
                "Failed to fetch subcate=%d page=%d: %s — continuing to next page",
                subcate_id, page, exc,
            )

    logger.info(
        "Category subcate=%d complete: %d/%d pages, %d items total",
        subcate_id, total_pages, first["max_page"], len(all_items),
    )
    return all_items
