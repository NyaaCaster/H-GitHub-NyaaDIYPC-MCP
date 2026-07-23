"""
预算收敛 — 方案总价超/低于预算区间时，按优先序升降档调整。

D6 §2.5：不动 GPU/CPU 达标线，优先调整次要品类。
"""

import logging
import sqlite3

from . import CONVERGE_MAX_ITER

logger = logging.getLogger(__name__)

# 降档优先级（先降对体验影响小的）
_DOWNGRADE_ORDER = ["case", "cooler", "ssd", "psu", "memory"]
# 升档优先级（先升体验核心件）
_UPGRADE_ORDER = ["gpu", "cpu", "memory", "ssd"]


def _effective_price(row: dict) -> int | None:
    for k in ("price_jd", "price_show", "price_min"):
        p = row.get(k)
        if p is not None and p > 0:
            return p
    return None


def _total(items: list[dict]) -> int:
    return sum(it.get("price", 0) for it in items)


def converge_budget(
    db_path: str,
    items: list[dict],
    budget_min: int,
    budget_max: int,
) -> list[dict]:
    """预算收敛：调整方案使总价落在 [budget_min, budget_max] 区间。

    Args:
        db_path: SQLite 路径。
        items: 当前件列表。
        budget_min: 预算下限。
        budget_max: 预算上限。

    Returns:
        调整后的 items 列表。
    """
    current = list(items)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    for i in range(CONVERGE_MAX_ITER):
        t = _total(current)
        if budget_min <= t <= budget_max:
            break

        if t > budget_max:
            # 降档：找非核心品类中更便宜的替代件
            downgraded = False
            for cat in _DOWNGRADE_ORDER:
                cat_items = [it for it in current if it.get("category") == cat]
                if not cat_items:
                    continue
                # 查更便宜的替代
                current_price = cat_items[0].get("price", 0)
                if current_price <= 0:
                    continue
                rows = conn.execute(
                    """SELECT h.pro_id, h.model, h.brand, h.price_jd, h.tier_score
                       FROM hardware h WHERE h.category=? AND h.active=1
                       AND h.price_jd > 0 AND h.price_jd < ?
                       ORDER BY h.price_jd ASC LIMIT 1""",
                    (cat, current_price),
                ).fetchall()
                if rows:
                    r = dict(rows[0])
                    replacement = {
                        "category": cat, "pro_id": r["pro_id"],
                        "model": r["model"], "brand": r.get("brand", ""),
                        "price": r["price_jd"], "tier_score": r.get("tier_score"),
                    }
                    current = [replacement if it.get("category") == cat else it for it in current]
                    downgraded = True
                    logger.info("Converge downgrade: %s %d→%d", cat, current_price, r["price_jd"])
                    break
            if not downgraded:
                break  # 无法再降

        elif t < budget_min:
            # 升档：按核心度顺序升
            upgraded = False
            remaining = budget_max - t
            if remaining <= 0:
                break
            for cat in _UPGRADE_ORDER:
                cat_items = [it for it in current if it.get("category") == cat]
                if not cat_items:
                    continue
                current_price = cat_items[0].get("price", 0)
                target_max = current_price + remaining
                rows = conn.execute(
                    """SELECT h.pro_id, h.model, h.brand, h.price_jd, h.tier_score
                       FROM hardware h WHERE h.category=? AND h.active=1
                       AND h.price_jd > ? AND h.price_jd <= ?
                       ORDER BY h.tier_score DESC, h.price_jd ASC LIMIT 1""",
                    (cat, current_price, target_max),
                ).fetchall()
                if rows:
                    r = dict(rows[0])
                    replacement = {
                        "category": cat, "pro_id": r["pro_id"],
                        "model": r["model"], "brand": r.get("brand", ""),
                        "price": r["price_jd"], "tier_score": r.get("tier_score"),
                    }
                    current = [replacement if it.get("category") == cat else it for it in current]
                    upgraded = True
                    logger.info("Converge upgrade: %s %d→%d", cat, current_price, r["price_jd"])
                    break
            if not upgraded:
                break

    conn.close()
    return current
