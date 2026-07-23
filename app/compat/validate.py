"""
validate_build 编排层 — 加载件数据 → 跑全规则 → 算总价功耗 → 返回 CompatResult。

只读操作（审计整改 1）：不写库、不改状态。
"""

import json
import logging
import sqlite3
from typing import Optional

from . import CompatResult, Issue, IssueLevel
from .power import estimate_power, check_psu_headroom
from .rules import (
    check_c1_socket,
    check_c2_mem_mb,
    check_c3_mem_cpu,
    check_c4_form_factor,
    check_c5_gpu_len,
    check_c6_cooler_h,
    check_c7_cooler_socket,
    check_c8_psu_power,
    check_c9_storage,
    check_w1_k_z,
    check_w2_mem_freq,
    check_w3_psu_margin,
    check_w4_size_margin,
    check_w5_igpu,
)

logger = logging.getLogger(__name__)

# 有效的品类枚举
_VALID_CATEGORIES = frozenset({
    "cpu", "mainboard", "memory", "gpu", "hdd", "ssd", "psu", "cooler", "case",
})

# 单件品类（每个方案最多 1 件）
_SINGLE_CATEGORIES = frozenset({"cpu", "mainboard", "memory", "psu", "cooler", "case"})

# 可选件品类（可 0 件）
_OPTIONAL_CATEGORIES = frozenset({"gpu", "hdd", "ssd"})


def _get_db_row(cursor, pro_id: str, category: str) -> dict | None:
    """查询 hardware + compat 合并行。"""
    cursor.execute("""
        SELECT h.pro_id, h.category, h.model, h.brand,
               h.price_jd, h.price_show, h.price_min,
               c.socket, c.tdp_w, c.mem_type, c.igpu,
               c.form_factor, c.mem_slots, c.mb_chipset, c.m2_slots,
               c.mem_capacity_gb, c.mem_freq,
               c.vram_gb, c.gpu_len_mm, c.gpu_power_pin, c.gpu_rec_psu_w,
               c.interface, c.ss_form, c.capacity_gb, c.size_inch,
               c.rated_w, c.modular, c.cert,
               c.cooler_type, c.cooler_h_mm, c.cooler_sockets,
               c.case_ff, c.max_gpu_len_mm, c.max_cooler_h_mm, c.radiator_support
        FROM hardware h
        LEFT JOIN compat c ON h.pro_id = c.pro_id
        WHERE h.pro_id = ?
    """, (pro_id,))
    row = cursor.fetchone()
    if not row:
        return None

    return {
        # hardware
        "pro_id": row[0], "category": row[1], "model": row[2], "brand": row[3],
        "price_jd": row[4], "price_show": row[5], "price_min": row[6],
        # compat — mirroring compat table columns exactly
        "socket": row[7], "tdp_w": row[8], "mem_type": row[9], "igpu": row[10],
        "form_factor": row[11], "mem_slots": row[12], "mb_chipset": row[13], "m2_slots": row[14],
        "mem_capacity_gb": row[15], "mem_freq": row[16],
        "vram_gb": row[17], "gpu_len_mm": row[18], "gpu_power_pin": row[19], "gpu_rec_psu_w": row[20],
        "interface": row[21], "ss_form": row[22], "capacity_gb": row[23], "size_inch": row[24],
        "rated_w": row[25], "modular": row[26], "cert": row[27],
        "cooler_type": row[28], "cooler_h_mm": row[29], "cooler_sockets": row[30],
        "case_ff": row[31], "max_gpu_len_mm": row[32], "max_cooler_h_mm": row[33],
        "radiator_support": row[34],
    }


def _effective_price(row: dict) -> int | None:
    """从 hardware 行提取生效价格（复用 P3 pricing 模块逻辑）。"""
    for key in ("price_jd", "price_show", "price_min"):
        p = row.get(key)
        if p is not None and p > 0:
            return p
    return None


def load_build_items(
    db_path: str,
    items: list[dict],
) -> dict:
    """从 DB 加载装机清单各组件的 hardware + compat 数据。

    Args:
        db_path: SQLite 数据库路径。
        items: [{"category":"cpu","pro_id":"xxx"}, ...]

    Returns:
        {
            "ok": bool,
            "items": [dict, ...],          # 每件的完整行 dict
            "by_category": {                # 按品类分组
                "cpu": dict | None,
                "mainboard": dict | None,
                "memory": dict | None,
                "gpu": dict | None,
                "psu": dict | None,
                "cooler": dict | None,
                "case": dict | None,
                "ssds": [dict, ...],
                "hdds": [dict, ...],
            },
            "errors": [Issue, ...],         # 加载阶段的错误（件不存在/品类非法等）
        }
    """
    issues: list[Issue] = []
    loaded: list[dict] = []
    by_category: dict = {
        "cpu": None, "mainboard": None, "memory": None,
        "gpu": None, "psu": None, "cooler": None, "case": None,
        "ssds": [], "hdds": [],
    }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        for item in items:
            cat = (item.get("category") or "").lower().strip()
            pro_id = (item.get("pro_id") or "").strip()

            if cat not in _VALID_CATEGORIES:
                issues.append(Issue(
                    level=IssueLevel.ERROR,
                    rule="INPUT",
                    detail=f"未知品类 '{item.get('category')}'，有效值: {', '.join(sorted(_VALID_CATEGORIES))}",
                ))
                continue

            if not pro_id:
                issues.append(Issue(
                    level=IssueLevel.ERROR,
                    rule="INPUT",
                    detail=f"品类 {cat} 缺少 pro_id",
                ))
                continue

            row = _get_db_row(cursor, pro_id, cat)
            if row is None:
                issues.append(Issue(
                    level=IssueLevel.ERROR,
                    rule="INPUT",
                    detail=f"pro_id={pro_id}（品类 {cat}）在数据库中不存在",
                ))
                continue

            # 验证 category 一致性
            db_cat = row.get("category", "")
            if db_cat != cat:
                issues.append(Issue(
                    level=IssueLevel.WARN,
                    rule="INPUT",
                    detail=f"pro_id={pro_id} 数据库品类为 '{db_cat}'，但输入标注为 '{cat}'",
                ))

            loaded.append(row)

            # 分配到品类槽
            if cat in ("ssd", "hdd"):
                by_category[f"{cat}s"].append(row)
            elif cat == "gpu":
                # GPU 可 0 或 1
                if by_category["gpu"] is not None:
                    issues.append(Issue(
                        level=IssueLevel.WARN,
                        rule="INPUT",
                        detail="方案包含多张显卡，仅校验第一张",
                    ))
                else:
                    by_category["gpu"] = row
            else:
                if by_category.get(cat) is not None:
                    issues.append(Issue(
                        level=IssueLevel.WARN,
                        rule="INPUT",
                        detail=f"方案包含多件 {cat}（预期 1 件），仅校验第一件",
                    ))
                else:
                    by_category[cat] = row

    finally:
        conn.close()

    return {
        "ok": not any(i.level == IssueLevel.ERROR for i in issues),
        "items": loaded,
        "by_category": by_category,
        "errors": issues,
    }


def run_all_checks(build: dict) -> CompatResult:
    """对已加载的装机清单跑全部兼容规则 + 功耗估算 + 总价。

    Args:
        build: load_build_items() 返回的 dict。

    Returns:
        CompatResult 含 compat_ok / total / est_power / psu_headroom / issues。
    """
    by_cat = build["by_category"]
    issues: list[Issue] = list(build.get("errors", []))

    # 如果加载阶段已有 error（如件不存在），compat_ok 直接为 False
    has_load_error = any(i.level == IssueLevel.ERROR for i in issues)

    cpu = by_cat.get("cpu")
    mb = by_cat.get("mainboard")
    mem = by_cat.get("memory")
    gpu = by_cat.get("gpu")
    psu = by_cat.get("psu")
    cooler = by_cat.get("cooler")
    case = by_cat.get("case")
    ssds = by_cat.get("ssds", [])
    hdds = by_cat.get("hdds", [])

    # ---- 总价计算 ----
    total = 0
    total_unknown = False
    for item in build["items"]:
        p = _effective_price(item)
        if p is None:
            total_unknown = True
        else:
            total += p

    # ---- 功耗估算 ----
    cpu_tdp = cpu.get("tdp_w") if cpu else None
    gpu_tdp = gpu.get("tdp_w") if gpu else None
    gpu_rec = gpu.get("gpu_rec_psu_w") if gpu else None
    psu_rated = psu.get("rated_w") if psu else None

    # 无 CPU 且无 GPU → 功耗估算无意义（如空清单）
    if cpu_tdp is None and gpu_tdp is None and gpu_rec is None:
        est_power = 0.0
        headroom_info = {"ratio": None, "hard_ok": None, "recommend_ok": None}
    else:
        power_info = estimate_power(cpu_tdp, gpu_tdp, gpu_rec)
        est_power = power_info["est_power"]
        headroom_info = check_psu_headroom(psu_rated, est_power)

    # ---- 跑全部规则 ----

    # C1: CPU↔主板插槽
    if cpu and mb:
        r = check_c1_socket(cpu.get("socket"), mb.get("socket"))
        if r: issues.append(r)

    # C2: 内存类型↔主板
    if mem and mb:
        r = check_c2_mem_mb(mem.get("mem_type"), mb.get("mem_type"))
        if r: issues.append(r)

    # C3: 内存类型↔CPU
    if mem and cpu:
        r = check_c3_mem_cpu(mem.get("mem_type"), cpu.get("mem_type"))
        if r: issues.append(r)

    # C4: 主板板型↔机箱
    if mb and case:
        r = check_c4_form_factor(mb.get("form_factor"), case.get("case_ff"))
        if r: issues.append(r)

    # C5: 显卡长度↔机箱
    if gpu and case:
        r = check_c5_gpu_len(gpu.get("gpu_len_mm"), case.get("max_gpu_len_mm"))
        if r: issues.append(r)
    # 无 GPU 不触发 C5

    # C6: 散热器高度↔机箱
    if cooler and case:
        r = check_c6_cooler_h(cooler.get("cooler_h_mm"), case.get("max_cooler_h_mm"))
        if r: issues.append(r)

    # C7: 散热器↔CPU 插槽
    if cooler and cpu:
        r = check_c7_cooler_socket(cooler.get("cooler_sockets"), cpu.get("socket"))
        if r: issues.append(r)

    # C8: 电源功率
    if psu:
        r = check_c8_psu_power(psu.get("rated_w"), est_power)
        if r: issues.append(r)

    # C9: 存储接口↔主板
    if ssds or hdds:
        mb_m2 = mb.get("m2_slots") if mb else None
        issues.extend(check_c9_storage(ssds, hdds, mb_m2))

    # W1: K系CPU↔Z板
    if cpu and mb:
        r = check_w1_k_z(cpu.get("model"), mb.get("mb_chipset"))
        if r: issues.append(r)

    # W2: 内存频率建议（占位）
    if cpu and mem:
        r = check_w2_mem_freq(cpu.get("model"), mem.get("mem_freq"))
        if r: issues.append(r)

    # W3: 供电余量偏紧
    r = check_w3_psu_margin(headroom_info["ratio"])
    if r: issues.append(r)

    # W4: 尺寸余量偏紧
    issues.extend(check_w4_size_margin(
        gpu.get("gpu_len_mm") if gpu else None,
        cooler.get("cooler_h_mm") if cooler else None,
        case.get("max_gpu_len_mm") if case else None,
        case.get("max_cooler_h_mm") if case else None,
    ))

    # W5: 无独显且 CPU 无核显
    if cpu:
        r = check_w5_igpu(gpu is not None, cpu.get("igpu"))
        if r: issues.append(r)

    # ---- 汇总 ----
    compat_ok = not any(i.level == IssueLevel.ERROR for i in issues)

    return CompatResult(
        compat_ok=compat_ok and not has_load_error,
        total=None if total_unknown else total,
        est_power=est_power if est_power > 0 else None,
        psu_headroom=headroom_info["ratio"],
        issues=issues,
    )


def validate_build(db_path: str, items: list[dict]) -> dict:
    """校验一套装机配置的兼容性与功耗（顶层入口）。

    只读操作，不写库、不改状态。

    Args:
        db_path: SQLite 数据库路径。
        items: [{"category":"cpu","pro_id":"xxx"}, ...]

    Returns:
        dict 含 compat_ok / total / est_power / psu_headroom / issues。
    """
    build = load_build_items(db_path, items)
    result = run_all_checks(build)
    return result.as_dict()
