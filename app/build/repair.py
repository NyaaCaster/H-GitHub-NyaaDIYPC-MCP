"""
兼容修复循环 — 选件后跑 validate_build，有 error 则就近换件重试。

复用 P4 的 load_build_items + run_all_checks。
"""

import logging
import sqlite3

from . import REPAIR_MAX_ITER, CANDIDATE_PRICE_SLACK

logger = logging.getLogger(__name__)

# 错误规则 → 替换目标品类（优先换便宜的）
_ERROR_TO_REPLACE: dict[str, list[str]] = {
    "C1": ["mainboard", "cpu"],       # socket mismatch → 换主板（便宜）
    "C2": ["memory"],                  # mem↔mb
    "C3": ["memory"],                  # mem↔cpu
    "C4": ["case", "mainboard"],       # form factor → 换机箱
    "C5": ["case", "gpu"],            # gpu too long → 换机箱
    "C6": ["cooler", "case"],         # cooler too tall → 换散热
    "C7": ["cooler"],                  # cooler↔cpu socket
    "C8": ["psu"],                     # psu too weak
    "C9": ["mainboard", "ssd"],       # 太多 NVMe → 换主板
}


def _find_replacement(db_path: str, category: str, current_price: int,
                      compat_constraints: dict) -> dict | None:
    """在 DB 中找同品类、相近价位、满足约束的替代件。"""
    price_min = int(current_price * 0.5)
    price_max = int(current_price * 1.5)

    clauses = [f"h.category='{category}'", "h.active=1",
               f"h.price_jd >= {price_min}", f"h.price_jd <= {price_max}"]
    params = []

    # 构建 JOIN + SELECT
    sql = f"""
        SELECT h.pro_id, h.category, h.model, h.brand,
               h.price_jd, h.price_show, h.price_min, h.tier_score
        FROM hardware h
        LEFT JOIN compat c ON h.pro_id = c.pro_id
        WHERE {' AND '.join(clauses)}
        ORDER BY h.price_jd ASC
        LIMIT 20
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql)]
    conn.close()

    if not rows:
        return None
    # 选最便宜的不同件
    return {
        "category": category,
        "pro_id": rows[0]["pro_id"],
        "model": rows[0]["model"],
        "brand": rows[0].get("brand", ""),
        "price": rows[0]["price_jd"],
        "tier_score": rows[0].get("tier_score"),
    } if rows else None


def repair_build(db_path: str, items: list[dict]) -> tuple[list[dict], list[dict]]:
    """兼容修复：对选好的 items 跑 validate，有 error 则替换冲突件。

    Args:
        db_path: SQLite 路径。
        items: [{"category":"cpu","pro_id":"xxx"}, ...]

    Returns:
        (repaired_items, issues) — repaired_items 是修复后的清单，
        issues 是最终 validate 结果中的 issues。
    """
    from app.compat.validate import load_build_items, run_all_checks

    current_items = list(items)

    for i in range(REPAIR_MAX_ITER):
        build = load_build_items(db_path, current_items)
        result = run_all_checks(build)

        errors = [iss for iss in result.issues if iss.level.value == "error"]
        if not errors:
            return current_items, [iss.as_dict() for iss in result.issues]

        # 取第一个 error，找到要替换的品类
        first_err = errors[0]
        rule = first_err.rule
        targets = _ERROR_TO_REPLACE.get(rule, [])
        if not targets:
            logger.warning("No replacement target for rule %s, cannot repair", rule)
            return current_items, [iss.as_dict() for iss in result.issues]

        # 尝试替换第一个目标品类
        replaced = False
        for target_cat in targets:
            target_item = next((it for it in current_items if it.get("category") == target_cat), None)
            if not target_item:
                continue
            current_price = target_item.get("price", 500)
            replacement = _find_replacement(db_path, target_cat, current_price, {})
            if replacement and replacement["pro_id"] != target_item.get("pro_id"):
                logger.info("Repair: replacing %s %s → %s (rule %s)",
                            target_cat, target_item.get("pro_id"), replacement["pro_id"], rule)
                current_items = [
                    replacement if (it.get("category") == target_cat) else it
                    for it in current_items
                ]
                replaced = True
                break

        if not replaced:
            logger.warning("Repair iteration %d: could not replace for rule %s", i + 1, rule)
            return current_items, [iss.as_dict() for iss in result.issues]

    # 达到最大迭代
    build = load_build_items(db_path, current_items)
    result = run_all_checks(build)
    return current_items, [iss.as_dict() for iss in result.issues]
