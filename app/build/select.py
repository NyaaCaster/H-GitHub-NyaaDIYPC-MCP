"""
逐类选件 — 按依赖顺序为每个品类在预算内选最优件。

依赖顺序（§2.3）：gpu → cpu → mainboard → memory → cooler → psu → case → ssd
D3 铁律：价格使用 effective_price（jd > show > min）。
"""

import json
import logging
import sqlite3
from typing import Optional

from . import CANDIDATE_PRICE_SLACK, DemandHit, ALL_CATEGORIES

logger = logging.getLogger(__name__)

_HW_COLS = "h.pro_id, h.category, h.model, h.brand, h.price_jd, h.price_show, h.price_min, h.tier_score, h.popularity"
_COMPAT_JOIN = "LEFT JOIN compat c ON h.pro_id = c.pro_id"


def _effective_price(row: dict) -> int | None:
    for k in ("price_jd", "price_show", "price_min"):
        p = row.get(k)
        if p is not None and p > 0:
            return p
    return None


def _db_rows(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql, params)]
    conn.close()
    return rows


def _pick_cheapest(
    db_path: str, category: str,
    where_clauses: list[str], where_params: list,
    max_price: int,
    order_by: str = "price_jd ASC",
) -> dict | None:
    """在预算内选最便宜的件。"""
    clauses = [f"h.category='{category}'", "h.active=1", "h.price_jd>0"]
    clauses.extend(where_clauses)
    clauses.append(f"h.price_jd <= {max_price}")
    where = " AND ".join(clauses)
    sql = f"SELECT {_HW_COLS}, c.* FROM hardware h {_COMPAT_JOIN} WHERE {where} ORDER BY {order_by} LIMIT 1"
    rows = _db_rows(db_path, sql, tuple(where_params))
    return rows[0] if rows else None


def _pick_best(
    db_path: str, category: str,
    where_clauses: list[str], where_params: list,
    max_price: int,
    order_by: str = "h.price_jd ASC",
    limit: int = 1,
) -> list[dict]:
    """在预算内选最符合条件的件（可多件）。"""
    clauses = [f"h.category='{category}'", "h.active=1", "h.price_jd>0"]
    clauses.extend(where_clauses)
    # 允许略超预算（CANDIDATE_PRICE_SLACK）
    clauses.append(f"h.price_jd <= {int(max_price * CANDIDATE_PRICE_SLACK)}")
    where = " AND ".join(clauses)
    sql = f"SELECT {_HW_COLS}, c.* FROM hardware h {_COMPAT_JOIN} WHERE {where} ORDER BY {order_by} LIMIT {limit}"
    return _db_rows(db_path, sql, tuple(where_params))


def _item_dict(row: dict) -> dict:
    """将 DB row 转为 build_pc 输出格式的 item。"""
    price = _effective_price(row)
    return {
        "category": row["category"],
        "pro_id": row["pro_id"],
        "model": row["model"],
        "brand": row.get("brand", ""),
        "price": price or 0,
        "tier_score": row.get("tier_score"),
    }


# ---- 各品类选择函数 ----

def select_gpu(db_path: str, budget: int, demand: DemandHit) -> dict | None:
    """选显卡：满足 tier 门槛 + 预算内。"""
    budget = max(budget, 500)  # 最低显卡预算
    # 先尝试 rec_tier
    if demand.rec_gpu_tier:
        rows = _pick_best(db_path, "gpu",
                          ["h.tier_score >= ?"], [demand.rec_gpu_tier],
                          budget, "h.price_jd ASC")
        if rows:
            return _item_dict(rows[0])
    # 降 min_tier
    if demand.min_gpu_tier:
        rows = _pick_best(db_path, "gpu",
                          ["h.tier_score >= ?"], [demand.min_gpu_tier],
                          budget, "h.tier_score DESC, h.price_jd ASC")
        if rows:
            return _item_dict(rows[0])
    # 无 tier 门槛，纯预算选
    rows = _pick_best(db_path, "gpu", [], [], budget, "h.tier_score DESC, h.price_jd ASC")
    return _item_dict(rows[0]) if rows else None


def select_cpu(db_path: str, budget: int, demand: DemandHit) -> dict | None:
    """选 CPU：满足 tier 门槛 + 预算内。"""
    budget = max(budget, 400)
    if demand.rec_cpu_tier:
        rows = _pick_best(db_path, "cpu",
                          ["h.tier_score >= ?"], [demand.rec_cpu_tier],
                          budget, "h.price_jd ASC")
        if rows:
            return _item_dict(rows[0])
    if demand.min_cpu_tier:
        rows = _pick_best(db_path, "cpu",
                          ["h.tier_score >= ?"], [demand.min_cpu_tier],
                          budget, "h.tier_score DESC, h.price_jd ASC")
        if rows:
            return _item_dict(rows[0])
    rows = _pick_best(db_path, "cpu", [], [], budget, "h.tier_score DESC, h.price_jd ASC")
    return _item_dict(rows[0]) if rows else None


def select_mainboard(db_path: str, budget: int, cpu_item: dict) -> dict | None:
    """选主板：跟随 CPU socket + mem_type。"""
    budget = max(budget, 300)
    cpu_row = _db_rows(db_path,
                       f"SELECT {_HW_COLS}, c.* FROM hardware h {_COMPAT_JOIN} WHERE h.pro_id=?",
                       (cpu_item["pro_id"],))
    if not cpu_row:
        return None
    cpu = cpu_row[0]
    socket = cpu.get("socket")
    mem_type = cpu.get("mem_type")
    cpu_model = cpu.get("model", "")

    # 芯片组偏好：K 系 → Z，非 K → B/H
    chipset_hint = None
    if cpu_model and "K" in cpu_model.upper() and "INTEL" in cpu_model.upper():
        chipset_hint = "Z"

    clauses = []
    params = []
    if socket:
        clauses.append("c.socket = ?")
        params.append(socket)
    if mem_type:
        clauses.append("c.mem_type = ?")
        params.append(mem_type)

    rows = _pick_best(db_path, "mainboard", clauses, params, budget,
                      "h.price_jd ASC")
    if not rows:
        # 放宽：不限制 mem_type
        clauses2 = [c for c in clauses if "mem_type" not in c]
        params2 = [p for (c, p) in zip(clauses, params) if "mem_type" not in c]
        rows = _pick_best(db_path, "mainboard", clauses2, params2, budget,
                          "h.price_jd ASC")

    if not rows:
        return None

    # 芯片组优先
    if chipset_hint:
        for r in rows:
            chipset = (r.get("mb_chipset") or "").upper()
            if chipset.startswith(chipset_hint):
                return _item_dict(r)

    return _item_dict(rows[0])


def select_memory(db_path: str, budget: int, cpu_item: dict, demand: DemandHit) -> dict | None:
    """选内存：跟随 CPU mem_type + 满足容量。"""
    budget = max(budget, 200)
    cpu_row = _db_rows(db_path,
                       f"SELECT {_HW_COLS}, c.* FROM hardware h {_COMPAT_JOIN} WHERE h.pro_id=?",
                       (cpu_item["pro_id"],))
    mem_type = cpu_row[0].get("mem_type") if cpu_row else None
    min_cap = (demand.min_ram_gb or 16) // 2  # 双通道，单条容量

    clauses = []
    params = []
    if mem_type:
        clauses.append("c.mem_type = ?")
        params.append(mem_type)
    if min_cap:
        clauses.append("c.mem_capacity_gb >= ?")
        params.append(min_cap)

    rows = _pick_best(db_path, "memory", clauses, params, budget,
                      "c.mem_freq DESC, h.price_jd ASC")
    return _item_dict(rows[0]) if rows else None


def select_cooler(db_path: str, budget: int, cpu_item: dict) -> dict | None:
    """选散热器：支持 CPU 插槽，高 TDP 优先塔散。"""
    budget = max(budget, 50)
    cpu_row = _db_rows(db_path,
                       f"SELECT {_HW_COLS}, c.* FROM hardware h {_COMPAT_JOIN} WHERE h.pro_id=?",
                       (cpu_item["pro_id"],))
    if not cpu_row:
        return None
    cpu = cpu_row[0]
    cpu_socket = cpu.get("socket")
    cpu_tdp = cpu.get("tdp_w") or 65

    clauses = []
    params = []
    # 散热器插槽匹配用 JSON 数组，做 LIKE 匹配
    # 因 SQLite 不支持 json 函数，用 LIKE
    if cpu_socket:
        clauses.append("(c.cooler_sockets LIKE ? OR c.cooler_sockets IS NULL)")
        params.append(f"%{cpu_socket}%")

    rows = _pick_best(db_path, "cooler", clauses, params, budget,
                      "h.price_jd ASC", limit=10)
    if not rows:
        return None

    # 高 TDP CPU (>125W) 优先塔散/水冷
    if cpu_tdp > 125:
        for r in rows:
            ctype = (r.get("cooler_type") or "").lower()
            if "风冷" in ctype or "水冷" in ctype or "热管" in ctype:
                return _item_dict(r)

    return _item_dict(rows[0])


def select_psu(db_path: str, budget: int, est_power_hint: float = 0) -> dict | None:
    """选电源：额定功率满足余量。"""
    budget = max(budget, 150)
    min_w = int(est_power_hint * 1.3) if est_power_hint > 0 else 0
    ideal_w = int(est_power_hint * 1.5) if est_power_hint > 0 else 0

    clauses = []
    params = []
    if min_w > 0:
        clauses.append("c.rated_w >= ?")
        params.append(min_w)

    rows = _pick_best(db_path, "psu", clauses, params, budget,
                      "c.rated_w ASC, h.price_jd ASC", limit=10)
    if not rows:
        # 放宽条件
        rows = _pick_best(db_path, "psu", [], [], budget,
                          "c.rated_w ASC, h.price_jd ASC", limit=10)

    if not rows:
        return None

    # 优先满足理想余量
    if ideal_w > 0:
        for r in rows:
            if (r.get("rated_w") or 0) >= ideal_w:
                return _item_dict(r)

    return _item_dict(rows[0])


def select_case(db_path: str, budget: int, mb_item: dict | None,
                gpu_item: dict | None, cooler_item: dict | None) -> dict | None:
    """选机箱：支持主板板型 + 容纳显卡/散热器。"""
    budget = max(budget, 100)

    clauses = []
    params = []

    # 主板板型
    if mb_item:
        mb_row = _db_rows(db_path,
                          f"SELECT {_HW_COLS}, c.* FROM hardware h {_COMPAT_JOIN} WHERE h.pro_id=?",
                          (mb_item["pro_id"],))
        if mb_row:
            ff = mb_row[0].get("form_factor")
            if ff:
                clauses.append("(c.case_ff LIKE ? OR c.case_ff IS NULL)")
                params.append(f"%{ff}%")

    # 显卡长度
    if gpu_item:
        gpu_row = _db_rows(db_path,
                           f"SELECT {_HW_COLS}, c.* FROM hardware h {_COMPAT_JOIN} WHERE h.pro_id=?",
                           (gpu_item["pro_id"],))
        if gpu_row:
            gpu_len = gpu_row[0].get("gpu_len_mm")
            if gpu_len:
                clauses.append("(c.max_gpu_len_mm >= ? OR c.max_gpu_len_mm IS NULL)")
                params.append(gpu_len)

    # 散热器高度
    if cooler_item:
        cooler_row = _db_rows(db_path,
                              f"SELECT {_HW_COLS}, c.* FROM hardware h {_COMPAT_JOIN} WHERE h.pro_id=?",
                              (cooler_item["pro_id"],))
        if cooler_row:
            cooler_h = cooler_row[0].get("cooler_h_mm")
            if cooler_h:
                clauses.append("(c.max_cooler_h_mm >= ? OR c.max_cooler_h_mm IS NULL)")
                params.append(cooler_h)

    rows = _pick_best(db_path, "case", clauses, params, budget, "h.price_jd ASC")
    if not rows:
        rows = _pick_best(db_path, "case", [], [], budget, "h.price_jd ASC")

    return _item_dict(rows[0]) if rows else None


def select_storage(db_path: str, budget: int) -> list[dict]:
    """选存储：NVMe SSD 优先，≥500GB，预算内尽可能大。"""
    budget = max(budget, 150)
    rows = _pick_best(db_path, "ssd",
                      ["(c.interface LIKE '%NVMe%' OR c.interface LIKE '%M.2%')",
                       "c.capacity_gb >= 500"],
                      [], budget,
                      "c.capacity_gb DESC, h.price_jd ASC", limit=3)
    if not rows:
        rows = _pick_best(db_path, "ssd", ["c.capacity_gb >= 500"], [], budget,
                          "c.capacity_gb DESC, h.price_jd ASC", limit=3)
    if not rows:
        rows = _pick_best(db_path, "ssd", [], [], budget,
                          "c.capacity_gb DESC, h.price_jd ASC", limit=3)

    items = []
    remaining = budget
    for r in rows:
        p = _effective_price(r) or 0
        if p <= remaining:
            items.append(_item_dict(r))
            remaining -= p
        if remaining < 100:
            break
    # 至少选 1 件
    if not items and rows:
        items.append(_item_dict(rows[0]))
    return items


# ---- 总编排 ----

def select_all(
    db_path: str,
    total_budget: int,
    allocation: dict[str, int],
    demand: DemandHit,
) -> dict:
    """按依赖顺序为全品类选件。

    Returns:
        {
          "ok": bool,
          "gpu": dict|None, "cpu": dict|None, "mainboard": dict|None,
          "memory": dict|None, "cooler": dict|None, "psu": dict|None,
          "case": dict|None, "ssds": [dict, ...],
          "items": [dict, ...],    # 全部选中件的扁平列表
          "total": int,
          "violations": [str, ...],
        }
    """
    violations: list[str] = []
    gpu = cpu = mb = mem = cooler = psu = case = None
    ssds: list[dict] = []

    # 1. GPU
    gpu = select_gpu(db_path, allocation.get("gpu", 0), demand)
    if not gpu:
        violations.append(f"GPU 预算 {allocation.get('gpu', 0)} 元内无满足 tier {demand.min_gpu_tier} 的件")

    # 2. CPU
    cpu = select_cpu(db_path, allocation.get("cpu", 0), demand)
    if not cpu:
        violations.append(f"CPU 预算 {allocation.get('cpu', 0)} 元内无满足 tier {demand.min_cpu_tier} 的件")

    # GPU 和 CPU 缺一不可
    if not gpu or not cpu:
        items = [i for i in [gpu, cpu] if i]
        return {
            "ok": False, "gpu": gpu, "cpu": cpu,
            "mainboard": None, "memory": None, "cooler": None,
            "psu": None, "case": None, "ssds": [],
            "items": items,
            "total": sum(i.get("price", 0) for i in items),
            "violations": violations,
        }

    # 3. 主板
    mb = select_mainboard(db_path, allocation.get("mainboard", 0), cpu)

    # 4. 内存
    mem = select_memory(db_path, allocation.get("memory", 0), cpu, demand)

    # 5. 散热器
    cooler = select_cooler(db_path, allocation.get("cooler", 0), cpu)

    # 粗估功耗（用于选电源）
    cpu_tdp = 65
    gpu_tdp = 150
    if cpu:
        cpu_row = _db_rows(db_path,
                           f"SELECT c.tdp_w FROM compat c WHERE c.pro_id=?", (cpu["pro_id"],))
        if cpu_row:
            cpu_tdp = cpu_row[0].get("tdp_w") or 65
    if gpu:
        gpu_row = _db_rows(db_path,
                           f"SELECT c.tdp_w, c.gpu_rec_psu_w FROM compat c WHERE c.pro_id=?", (gpu["pro_id"],))
        if gpu_row:
            gpu_tdp = gpu_row[0].get("tdp_w") or gpu_row[0].get("gpu_rec_psu_w") or 150

    from app.compat.power import estimate_power
    est = estimate_power(cpu_tdp, gpu_tdp)["est_power"]

    # 6. 电源
    psu = select_psu(db_path, allocation.get("psu", 0), est)

    # 7. 机箱
    case = select_case(db_path, allocation.get("case", 0), mb, gpu, cooler)

    # 8. 存储
    ssds = select_storage(db_path, allocation.get("ssd", 0))

    # 未选到的品类用最低价兜底
    for cat, item, budget in [
        ("mainboard", mb, allocation.get("mainboard", 0)),
        ("memory", mem, allocation.get("memory", 0)),
        ("cooler", cooler, allocation.get("cooler", 0)),
        ("psu", psu, allocation.get("psu", 0)),
        ("case", case, allocation.get("case", 0)),
    ]:
        if not item:
            rows = _pick_best(db_path, cat, [], [], budget or 300, "h.price_jd ASC")
            if rows:
                item = _item_dict(rows[0])
                if cat == "mainboard": mb = item
                elif cat == "memory": mem = item
                elif cat == "cooler": cooler = item
                elif cat == "psu": psu = item
                elif cat == "case": case = item

    all_items = [i for i in [gpu, cpu, mb, mem, cooler, psu, case] if i] + ssds
    total = sum(i.get("price", 0) for i in all_items)

    return {
        "ok": True,
        "gpu": gpu, "cpu": cpu, "mainboard": mb,
        "memory": mem, "cooler": cooler, "psu": psu, "case": case,
        "ssds": ssds,
        "items": all_items,
        "total": total,
        "est_power": est,
        "violations": violations,
    }
