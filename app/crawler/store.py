"""
幂等 UPSERT 落库 — hardware / compat / perf_tier / meta。

约束（不可妥协）：
  - 写库前先备份 .db（SSOT §7）
  - 以 pro_id UPSERT（存在更新，不存在插入）
  - 停售件标 active=0，不物理删除
  - 一次刷新在事务内提交
"""

import json
import logging
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================
# SQL 模板
# ============================================================

UPSERT_HARDWARE = """
INSERT INTO hardware
  (pro_id, category, manu_id, model,
   price_jd, price_show, price_min,
   popularity, specs_json,
   active, deep_fetched_at, fetched_at)
VALUES
  (:pro_id, :category, :manu_id, :model,
   :price_jd, :price_show, :price_min,
   :popularity, :specs_json,
   1, :deep_fetched_at, :fetched_at)
ON CONFLICT(pro_id) DO UPDATE SET
  category   = excluded.category,
  manu_id    = excluded.manu_id,
  model      = excluded.model,
  price_jd   = COALESCE(excluded.price_jd, hardware.price_jd),
  price_show = COALESCE(excluded.price_show, hardware.price_show),
  price_min  = COALESCE(excluded.price_min, hardware.price_min),
  popularity = excluded.popularity,
  specs_json = COALESCE(excluded.specs_json, hardware.specs_json),
  active     = 1,
  deep_fetched_at = COALESCE(excluded.deep_fetched_at, hardware.deep_fetched_at),
  fetched_at = excluded.fetched_at
"""

UPSERT_COMPAT = """
INSERT INTO compat
  (pro_id, category,
   socket, tdp_w, mem_type, igpu,
   form_factor, mem_slots, mb_chipset, m2_slots,
   mem_capacity_gb, mem_freq,
   vram_gb, gpu_len_mm, gpu_power_pin, gpu_rec_psu_w,
   interface, ss_form, capacity_gb, size_inch,
   rated_w, modular, cert,
   cooler_type, cooler_h_mm, cooler_sockets,
   case_ff, max_gpu_len_mm, max_cooler_h_mm, radiator_support)
VALUES
  (:pro_id, :category,
   :socket, :tdp_w, :mem_type, :igpu,
   :form_factor, :mem_slots, :mb_chipset, :m2_slots,
   :mem_capacity_gb, :mem_freq,
   :vram_gb, :gpu_len_mm, :gpu_power_pin, :gpu_rec_psu_w,
   :interface, :ss_form, :capacity_gb, :size_inch,
   :rated_w, :modular, :cert,
   :cooler_type, :cooler_h_mm, :cooler_sockets,
   :case_ff, :max_gpu_len_mm, :max_cooler_h_mm, :radiator_support)
ON CONFLICT(pro_id) DO UPDATE SET
  category   = excluded.category,
  socket     = COALESCE(excluded.socket, compat.socket),
  tdp_w      = COALESCE(excluded.tdp_w, compat.tdp_w),
  mem_type   = COALESCE(excluded.mem_type, compat.mem_type),
  igpu       = COALESCE(excluded.igpu, compat.igpu),
  form_factor = COALESCE(excluded.form_factor, compat.form_factor),
  mem_slots  = COALESCE(excluded.mem_slots, compat.mem_slots),
  mb_chipset = COALESCE(excluded.mb_chipset, compat.mb_chipset),
  m2_slots   = COALESCE(excluded.m2_slots, compat.m2_slots),
  mem_capacity_gb = COALESCE(excluded.mem_capacity_gb, compat.mem_capacity_gb),
  mem_freq   = COALESCE(excluded.mem_freq, compat.mem_freq),
  vram_gb    = COALESCE(excluded.vram_gb, compat.vram_gb),
  gpu_len_mm = COALESCE(excluded.gpu_len_mm, compat.gpu_len_mm),
  gpu_power_pin = COALESCE(excluded.gpu_power_pin, compat.gpu_power_pin),
  gpu_rec_psu_w = COALESCE(excluded.gpu_rec_psu_w, compat.gpu_rec_psu_w),
  interface  = COALESCE(excluded.interface, compat.interface),
  ss_form    = COALESCE(excluded.ss_form, compat.ss_form),
  capacity_gb = COALESCE(excluded.capacity_gb, compat.capacity_gb),
  size_inch  = COALESCE(excluded.size_inch, compat.size_inch),
  rated_w    = COALESCE(excluded.rated_w, compat.rated_w),
  modular    = COALESCE(excluded.modular, compat.modular),
  cert       = COALESCE(excluded.cert, compat.cert),
  cooler_type = COALESCE(excluded.cooler_type, compat.cooler_type),
  cooler_h_mm = COALESCE(excluded.cooler_h_mm, compat.cooler_h_mm),
  cooler_sockets = COALESCE(excluded.cooler_sockets, compat.cooler_sockets),
  case_ff    = COALESCE(excluded.case_ff, compat.case_ff),
  max_gpu_len_mm  = COALESCE(excluded.max_gpu_len_mm, compat.max_gpu_len_mm),
  max_cooler_h_mm = COALESCE(excluded.max_cooler_h_mm, compat.max_cooler_h_mm),
  radiator_support = COALESCE(excluded.radiator_support, compat.radiator_support)
"""

UPSERT_TIER = """
INSERT INTO perf_tier
  (pro_id, kind, dimension, model, score, rank, ratio, firm, fetched_at)
VALUES
  (:pro_id, :kind, :dimension, :model, :score, :rank, :ratio, :firm, :fetched_at)
ON CONFLICT(pro_id, kind, dimension) DO UPDATE SET
  model  = excluded.model,
  score  = excluded.score,
  rank   = excluded.rank,
  ratio  = excluded.ratio,
  firm   = excluded.firm,
  fetched_at = excluded.fetched_at
"""

MARK_INACTIVE = """
UPDATE hardware
SET active = 0
WHERE category = ? AND pro_id NOT IN ({placeholders})
"""

UPSERT_META = """
INSERT INTO meta(key, value) VALUES(?, ?)
ON CONFLICT(key) DO UPDATE SET value = excluded.value
"""

# ============================================================
# 备份
# ============================================================


def backup_db(db_path: str) -> str:
    """复制 .db → <db_path>.<timestamp>.bak，返回备份路径。"""
    if not os.path.exists(db_path):
        logger.info("DB does not exist yet, skipping backup: %s", db_path)
        return ""

    ts = time.strftime("%Y%m%d_%H%M%S")
    bak_path = f"{db_path}.{ts}.bak"
    shutil.copy2(db_path, bak_path)
    logger.info("DB backed up: %s → %s", db_path, bak_path)
    return bak_path


# ============================================================
# Upsert 函数
# ============================================================


def _now_iso() -> str:
    """返回当前 UTC ISO 时间戳。"""
    return datetime.now(timezone.utc).isoformat()


def upsert_hardware(
    conn: sqlite3.Connection,
    items: list[dict],
    category: str,
) -> int:
    """批量 UPSERT hardware 表。返回写入行数。"""
    now = _now_iso()
    rows: list[dict] = []

    for item in items:
        prices = item.get("prices", {})
        # specs_json：合并内嵌+深参
        specs = {**item.get("embed_params", {}), **item.get("deep_params", {})}
        specs_json = json.dumps(specs, ensure_ascii=False) if specs else None

        deep_at = now if item.get("deep_params") else item.get("deep_fetched_at")

        rows.append({
            "pro_id": item["pro_id"],
            "category": category,
            "manu_id": item.get("manu_id", ""),
            "model": item.get("model", ""),
            "price_jd": prices.get("price_jd"),
            "price_show": prices.get("price_show"),
            "price_min": prices.get("price_min"),
            "popularity": item.get("popularity", 0),
            "specs_json": specs_json,
            "deep_fetched_at": deep_at,
            "fetched_at": now,
        })

    conn.executemany(UPSERT_HARDWARE, rows)
    logger.info("hardware upserted: %d rows (category=%s)", len(rows), category)
    return len(rows)


def upsert_compat(
    conn: sqlite3.Connection,
    items: list[dict],
) -> int:
    """批量 UPSERT compat 表。item 需含 pro_id + compat dict。返回写入行数。"""
    rows: list[dict] = []
    for item in items:
        compat = item.get("compat", {})
        if not compat:
            continue
        row = {"pro_id": item["pro_id"]}
        # 填入 compat 字段（使用 NULL 作为默认值）
        for col in [
            "category", "socket", "tdp_w", "mem_type", "igpu",
            "form_factor", "mem_slots", "mb_chipset", "m2_slots",
            "mem_capacity_gb", "mem_freq",
            "vram_gb", "gpu_len_mm", "gpu_power_pin", "gpu_rec_psu_w",
            "interface", "ss_form", "capacity_gb", "size_inch",
            "rated_w", "modular", "cert",
            "cooler_type", "cooler_h_mm", "cooler_sockets",
            "case_ff", "max_gpu_len_mm", "max_cooler_h_mm", "radiator_support",
        ]:
            row[col] = compat.get(col)
        rows.append(row)

    if rows:
        conn.executemany(UPSERT_COMPAT, rows)
        logger.info("compat upserted: %d rows", len(rows))
    return len(rows)


def upsert_tiers(
    conn: sqlite3.Connection,
    tiers: list[dict],
) -> int:
    """批量 UPSERT perf_tier 表。返回写入行数。"""
    now = _now_iso()
    rows = [{**t, "fetched_at": now} for t in tiers]
    conn.executemany(UPSERT_TIER, rows)
    logger.info("perf_tier upserted: %d rows", len(rows))
    return len(rows)


def mark_inactive(
    conn: sqlite3.Connection,
    category: str,
    active_pro_ids: set[str],
) -> int:
    """将该品类不在 active_pro_ids 中的件标为 active=0。

    Args:
        active_pro_ids: 本轮抓取到的所有 pro_id 集合。
    Returns:
        被标为 inactive 的条目数。
    """
    if not active_pro_ids:
        logger.warning("No active pro_ids for category=%s, skip mark_inactive", category)
        return 0

    placeholders = ",".join("?" * len(active_pro_ids))
    sql = MARK_INACTIVE.replace("{placeholders}", placeholders)
    cursor = conn.execute(sql, [category] + list(active_pro_ids))
    count = cursor.rowcount
    if count:
        logger.info("Marked %d items inactive (category=%s)", count, category)
    return count


def record_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """写入 meta 表键值。"""
    conn.execute(UPSERT_META, (key, value))


def store_category(
    conn: sqlite3.Connection,
    category: str,
    items: list[dict],
    tiers: Optional[list[dict]] = None,
    db_path: str = "",
) -> None:
    """落库一个品类的全部数据（事务内完成）。

    Args:
        conn: SQLite 连接。
        category: 品类枚举。
        items: GetGoods 条目列表（每个 item 可选含 deep_params + compat）。
        tiers: 天梯数据列表（仅 cpu/gpu 品类有）。
        db_path: DB 路径（用于备份，可选）。
    """
    if not items:
        logger.warning("No items to store for category=%s", category)
        return

    logger.info("Storing category=%s: %d items", category, len(items))

    try:
        # 1) 备份
        if db_path:
            backup_db(db_path)

        # 2) 写入 hardware
        upsert_hardware(conn, items, category)

        # 3) 写入 compat
        compat_items = [it for it in items if it.get("compat")]
        if compat_items:
            upsert_compat(conn, compat_items)

        # 4) 标记停售
        active_ids = {it["pro_id"] for it in items}
        mark_inactive(conn, category, active_ids)

        # 5) 写入天梯
        if tiers:
            upsert_tiers(conn, tiers)

        # 6) 更新 meta 时间戳
        record_meta(conn, f"last_crawl_{category}", _now_iso())

        conn.commit()
        logger.info("Category %s stored and committed successfully", category)

    except Exception:
        conn.rollback()
        logger.error("Failed to store category=%s — transaction rolled back", category)
        raise
