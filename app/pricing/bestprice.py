"""
best-price 实时补价 — 调 best-price-mcp 获取淘宝/京东实时价 + 强制过滤层。

过滤层是审计整改 1 的强制执行：防串货/整机/笔记本/异常价污染报价数据。
best_price_mcp 为可选依赖——import 失败时自动降级为纯 ZOL 价模式，不阻断业务。
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from app.pricing import (
    EXCLUDE_KEYWORDS,
    PRICE_RT_MAX_RATIO,
    PRICE_RT_MIN_RATIO,
)
from app.pricing.effective import effective_price_from_row
from app.pricing.match import match_models

logger = logging.getLogger(__name__)

# 解析 ★/○ 标记行 — "★ 品牌型号 ￥12,345 (到手价...)"
RE_PRICE_LINE = re.compile(r"^[★○]\s*(.+?)\s*￥\s*([\d,]+)")

# 来源段头检测
RE_SECTION_JD = re.compile(r"(京东|jd)", re.I)
RE_SECTION_TAOBAO = re.compile(r"(淘宝|天猫|taobao|tmall)", re.I)


# ---- best-price 导入（可选依赖） ----


def _import_best_price() -> Optional[Any]:
    """尝试导入 best_price_mcp；失败返回 None（降级纯 ZOL 价模式）。"""
    try:
        import best_price_mcp
        return best_price_mcp
    except ImportError:
        logger.info("best_price_mcp not installed — real-time price disabled")
        return None


# ---- best-price 调用 ----


def fetch_realtime(query: str, platform: str = "all") -> Optional[str]:
    """调用 best-price-mcp compare_price() 获取格式化结果文本。

    Args:
        query: 搜索关键词（型号名）。
        platform: "jd" | "taobao" | "all"。

    Returns:
        格式化结果文本，或 None（不可用/失败）。
    """
    bp = _import_best_price()
    if bp is None:
        return None
    try:
        return bp.compare_price(query, platform)
    except Exception as exc:
        logger.warning("best-price query failed for %r: %s", query, exc)
        return None


# ---- 格式化输出解析 ----


def _parse_best_price_output(text: str) -> list[dict]:
    """从 best-price 格式化输出中提取结构化价格条目。

    best-price 返回格式示例：
        ===== 'RTX 5090' platform=all (5.3s) =====
        【京东】
        ★ 七彩虹 RTX 5090 ￥18,999 (到手价 18,999)
          ...
        ○ 某品牌 RTX 5090 ￥22,000
        【淘宝/天猫】
        ★ 某品牌 RTX 5090 ￥15,999
          ...

    Returns:
        [{"title": str, "price": int, "source": "jd"|"taobao", "recommended": bool}, ...]
    """
    results: list[dict] = []
    current_source = "unknown"

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # 检测来源段
        if RE_SECTION_JD.search(stripped):
            current_source = "jd"
            continue
        if RE_SECTION_TAOBAO.search(stripped):
            current_source = "taobao"
            continue

        # 匹配 ★/○ 条目
        m = RE_PRICE_LINE.match(stripped)
        if m:
            title = m.group(1).strip()
            price = int(m.group(2).replace(",", ""))
            results.append({
                "title": title,
                "price": price,
                "source": current_source,
                "recommended": stripped.startswith("★"),
            })

    return results


# ---- 强制过滤层 ----


def filter_price_entry(
    entry: dict,
    model: str,
    effective: int,
    category: str,
) -> Optional[dict]:
    """对单条 best-price 结果应用五层强制过滤。

    过滤规则（审计整改 1 + 2）：
      1. 整机/笔记本/主机排除
      2. 型号匹配（归一后核心 token 对齐）
      3. 异常低价排除（< 50% effective_price → 串货/配件）
      4. 异常高价排除（> 200% effective_price → 错配）
      5. 平台白名单（仅接受淘宝/京东）

    Args:
        entry: _parse_best_price_output 产出的单条 dict。
        model: ZOL 型号名（用于匹配）。
        effective: 该件的 effective_price（用于异常价判定）。
        category: 品类枚举。

    Returns:
        原 entry（通过过滤）或 None（被拦截）。
    """
    title = entry.get("title", "")
    rt_price = entry.get("price", 0)
    source = entry.get("source", "unknown")

    # 1) 整机/笔记本排除
    title_lower = title.lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in title_lower:
            logger.debug("Filter: excluded keyword '%s' in title: %s", kw, title[:60])
            return None

    # 2) 型号匹配
    if not match_models(model, title, category):
        logger.debug("Filter: model mismatch '%s' vs '%s'", model[:40], title[:60])
        return None

    # 3) 异常低价（串货/配件）
    if effective > 0 and rt_price < effective * PRICE_RT_MIN_RATIO:
        logger.debug(
            "Filter: price too low rt=%d < eff=%d * %.1f",
            rt_price, effective, PRICE_RT_MIN_RATIO,
        )
        return None

    # 4) 异常高价（错配）
    if effective > 0 and rt_price > effective * PRICE_RT_MAX_RATIO:
        logger.debug(
            "Filter: price too high rt=%d > eff=%d * %.1f",
            rt_price, effective, PRICE_RT_MAX_RATIO,
        )
        return None

    # 5) 平台白名单
    if source not in ("jd", "taobao"):
        logger.debug("Filter: source not whitelisted: %s", source)
        return None

    return entry


# ---- 批量补价 ----


def enrich_price_rt(
    items: list[dict],
    category: str,
    platform: str = "all",
    timeout_per_item: float = 8.0,
) -> list[dict]:
    """对方案中的件逐件查 best-price，填充 price_rt / price_rt_src / price_rt_at。

    行为：
      - 仅对 effective_price > 0 的件查询（无价件跳过）
      - 每件取第一个通过过滤的结果即停止
      - 失败/超时/过滤无一命中 → 不填充，方案照常
      - best-price 不可用 → 全部跳过，照常返回

    Args:
        items: 方案条目列表（dict 含 model / price_jd / price_show / price_min）。
        category: 品类枚举。
        platform: best-price 平台参数。
        timeout_per_item: 每件查询超时秒数（防止卡住）。

    Returns:
        原 items 列表（原地修改，price_rt / price_rt_src / price_rt_at 可能被填充）。
    """
    bp = _import_best_price()
    if bp is None:
        logger.info("best-price unavailable — skipping real-time price enrichment")
        return items

    enriched = 0
    for item in items:
        model = item.get("model", "")
        eff = effective_price_from_row(item)
        if not eff or not model:
            continue

        text = fetch_realtime(model, platform=platform)
        if not text:
            continue

        entries = _parse_best_price_output(text)
        if not entries:
            logger.debug("No price entries parsed for %s", model[:40])
            continue

        # 优先取 ★ 推荐项，再逐条过
        entries_sorted = sorted(entries, key=lambda e: (not e["recommended"], e["price"]))
        for entry in entries_sorted:
            filtered = filter_price_entry(entry, model, eff, category)
            if filtered:
                item["price_rt"] = filtered["price"]
                item["price_rt_src"] = filtered["source"]
                item["price_rt_at"] = datetime.now(timezone.utc).isoformat()
                enriched += 1
                logger.debug(
                    "RT price for %s: %d (%s) [%s]",
                    model[:50], filtered["price"], filtered["source"],
                    "★" if filtered["recommended"] else "○",
                )
                break  # 取第一个通过过滤的结果

    if enriched:
        logger.info("Real-time prices enriched: %d/%d items", enriched, len(items))
    else:
        logger.info("No real-time prices enriched (all filtered or unavailable)")

    return items
