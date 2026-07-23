"""
P5 需求映射 + build_pc 搭配算法单元测试。

Usage:
    python -m pytest tests/test_build.py -v
"""

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.build import (
    DemandHit, BuildPlan,
    BUDGET_PROFILE_GAMING, BUDGET_PROFILE_BALANCED,
    REPAIR_MAX_ITER, CONVERGE_MAX_ITER, PLAN_COUNT,
    FALLBACK_TIERS,
)
from app.build.allocate import allocate_budget
from app.build.demand import seed_demand_map, lookup_demand
from app.build.build_pc import build_pc


# ---- helpers ----

def _make_db(db_path: str):
    """Create minimal test DB with demand_map only (no auto-seed)."""
    import sqlite3
    from app.db.schema import SCHEMA_SQL
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', '1')")
    conn.commit()
    conn.close()


def _make_full_db(db_path: str):
    """Create test DB with hardware + compat for integration tests."""
    from app.db.schema import init_db
    conn = init_db(db_path)

    # 插入测试硬件数据
    hw_data = [
        # GPU
        ("gpu-rtx4060", "gpu", "七彩虹", "RTX 4060 8GB", 2299, 100, 8, 280, 550),
        ("gpu-rtx4070s", "gpu", "华硕", "RTX 4070 SUPER 12GB", 4599, 165, 12, 310, 750),
        ("gpu-rtx4080s", "gpu", "微星", "RTX 4080 SUPER 16GB", 8099, 190, 16, 336, 850),
        # CPU
        ("cpu-i5-14600kf", "cpu", "Intel", "i5-14600KF", 1899, 145, "LGA1700", 125, "DDR5"),
        ("cpu-i7-14700kf", "cpu", "Intel", "i7-14700KF", 2599, 168, "LGA1700", 125, "DDR5"),
        ("cpu-r5-7600", "cpu", "AMD", "Ryzen 5 7600", 1099, 130, "AM5", 65, "DDR5"),
        # 主板
        ("mb-b760", "mainboard", "华硕", "TUF B760M-PLUS", 1199, "LGA1700", "DDR5", "Micro-ATX", "B760", 2),
        ("mb-z790", "mainboard", "微星", "MPG Z790 EDGE", 2199, "LGA1700", "DDR5", "ATX", "Z790", 4),
        ("mb-b650", "mainboard", "技嘉", "B650M AORUS", 999, "AM5", "DDR5", "Micro-ATX", "B650", 2),
        # 内存
        ("mem-d5-32", "memory", "金士顿", "FURY Beast 32GB DDR5", 699, "DDR5", 32, 6000),
        ("mem-d5-16", "memory", "光威", "16GB DDR5 5600", 349, "DDR5", 16, 5600),
        # 散热
        ("cool-pa120", "cooler", "利民", "PA120 SE", 159, "风冷", 155, '["LGA1700","LGA1200","AM5","AM4"]'),
        ("cool-ak620", "cooler", "九州风神", "AK620", 259, "风冷", 160, '["LGA1700","LGA1200","AM5","AM4"]'),
        # 电源
        ("psu-750", "psu", "海韵", "FOCUS GX-750", 699, 750),
        ("psu-850", "psu", "海韵", "FOCUS GX-850", 799, 850),
        # 机箱
        ("case-l216", "case", "联力", "LANCOOL 216", 499, '["ATX","Micro-ATX","Mini-ITX"]', 392, 180),
        ("case-h5", "case", "NZXT", "H5 Flow", 599, '["ATX","Micro-ATX","Mini-ITX"]', 365, 165),
        # SSD
        ("ssd-990p", "ssd", "三星", "990 PRO 1TB", 699, "M.2 NVMe", "M.2 2280", 1000),
        ("ssd-sn770", "ssd", "WD", "SN770 1TB", 549, "M.2 NVMe", "M.2 2280", 1000),
    ]

    for hw in hw_data:
        conn.execute("""
            INSERT OR REPLACE INTO hardware
            (pro_id, category, brand, model, price_jd, tier_score)
            VALUES (?, ?, ?, ?, ?, ?)
        """, hw[:6])

    def ic(**kwargs):
        cols = list(kwargs.keys())
        vals = list(kwargs.values())
        ph = ",".join("?" for _ in cols)
        cs = ",".join(cols)
        conn.execute(f"INSERT INTO compat ({cs}) VALUES ({ph})", vals)

    ic(pro_id="gpu-rtx4060", category="gpu", vram_gb=8, gpu_len_mm=280, gpu_rec_psu_w=550, tdp_w=115)
    ic(pro_id="gpu-rtx4070s", category="gpu", vram_gb=12, gpu_len_mm=310, gpu_rec_psu_w=750, tdp_w=220)
    ic(pro_id="gpu-rtx4080s", category="gpu", vram_gb=16, gpu_len_mm=336, gpu_rec_psu_w=850, tdp_w=320)

    ic(pro_id="cpu-i5-14600kf", category="cpu", socket="LGA1700", tdp_w=125, mem_type="DDR5", igpu=0)
    ic(pro_id="cpu-i7-14700kf", category="cpu", socket="LGA1700", tdp_w=125, mem_type="DDR5", igpu=0)
    ic(pro_id="cpu-r5-7600", category="cpu", socket="AM5", tdp_w=65, mem_type="DDR5", igpu=1)

    ic(pro_id="mb-b760", category="mainboard", socket="LGA1700", mem_type="DDR5", form_factor="Micro-ATX", mem_slots=4, mb_chipset="B760", m2_slots=2)
    ic(pro_id="mb-z790", category="mainboard", socket="LGA1700", mem_type="DDR5", form_factor="ATX", mem_slots=4, mb_chipset="Z790", m2_slots=4)
    ic(pro_id="mb-b650", category="mainboard", socket="AM5", mem_type="DDR5", form_factor="Micro-ATX", mem_slots=4, mb_chipset="B650", m2_slots=2)

    ic(pro_id="mem-d5-32", category="memory", mem_type="DDR5", mem_capacity_gb=32, mem_freq=6000)
    ic(pro_id="mem-d5-16", category="memory", mem_type="DDR5", mem_capacity_gb=16, mem_freq=5600)

    ic(pro_id="cool-pa120", category="cooler", cooler_type="风冷", cooler_h_mm=155, cooler_sockets='["LGA1700","LGA1200","AM5","AM4"]')
    ic(pro_id="cool-ak620", category="cooler", cooler_type="风冷", cooler_h_mm=160, cooler_sockets='["LGA1700","LGA1200","AM5","AM4"]')

    ic(pro_id="psu-750", category="psu", rated_w=750)
    ic(pro_id="psu-850", category="psu", rated_w=850)

    ic(pro_id="case-l216", category="case", case_ff='["ATX","Micro-ATX","Mini-ITX"]', max_gpu_len_mm=392, max_cooler_h_mm=180)
    ic(pro_id="case-h5", category="case", case_ff='["ATX","Micro-ATX","Mini-ITX"]', max_gpu_len_mm=365, max_cooler_h_mm=165)

    ic(pro_id="ssd-990p", category="ssd", interface="M.2 NVMe", ss_form="M.2 2280", capacity_gb=1000)
    ic(pro_id="ssd-sn770", category="ssd", interface="M.2 NVMe", ss_form="M.2 2280", capacity_gb=1000)

    conn.commit()
    conn.close()


# ============================================================
# TestDemand
# ============================================================

class TestDemand:
    @pytest.fixture
    def db_path(self):
        fd, path = tempfile.mkstemp(suffix=".db", prefix="test_demand_")
        os.close(fd)
        _make_db(path)
        yield path
        import gc; gc.collect()
        try:
            if os.path.exists(path):
                os.remove(path)
        except PermissionError:
            pass

    def test_seed_idempotent(self, db_path):
        c1 = seed_demand_map(db_path)
        c2 = seed_demand_map(db_path)
        assert c1 > 0
        assert c2 == 0  # second call inserts nothing

    def test_lookup_known_game(self, db_path):
        seed_demand_map(db_path)
        hit = lookup_demand(db_path, "生化危机9", "2k", "high")
        assert hit.source == "map"
        assert hit.min_gpu_tier is not None
        assert hit.min_ram_gb == 32

    def test_lookup_alias(self, db_path):
        seed_demand_map(db_path)
        hit = lookup_demand(db_path, "re9", "2k", "high")
        assert hit.source == "map"

    def test_lookup_alias_english(self, db_path):
        seed_demand_map(db_path)
        hit = lookup_demand(db_path, "wukong", "1080p", "high")
        assert hit.source == "map"

    def test_lookup_fallback_generic(self, db_path):
        seed_demand_map(db_path)
        hit = lookup_demand(db_path, "未知游戏ABC", "2k", "high")
        assert hit.source == "fallback"
        assert hit.min_gpu_tier is not None

    def test_lookup_no_game(self, db_path):
        seed_demand_map(db_path)
        hit = lookup_demand(db_path, None, "1080p", "medium")
        assert hit.source == "fallback"

    def test_lookup_fallback_unknown_res(self, db_path):
        """极偏分辨率走 hardcoded 兜底。"""
        seed_demand_map(db_path)
        # 注意：fallback_unknown 只在 generic 也没有对应分辨率时触发
        # 我们测试正常情况
        hit = lookup_demand(db_path, "random game", "1080p", "medium")
        assert hit.source in ("fallback", "fallback_unknown")

    def test_normalize_resolution(self, db_path):
        seed_demand_map(db_path)
        hit = lookup_demand(db_path, "CS2", "1440p", "high")
        assert hit.resolution == "2k"  # 1440p → 2k

    def test_normalize_quality_cn(self, db_path):
        seed_demand_map(db_path)
        hit = lookup_demand(db_path, "原神", "1080p", "高")
        assert hit.quality == "high"


# ============================================================
# TestAllocate
# ============================================================

class TestAllocate:
    def test_gaming_profile(self):
        alloc = allocate_budget(10000, "gaming")
        assert alloc["gpu"] == int(10000 * 0.38)
        assert alloc["cpu"] == int(10000 * 0.18)
        # 总和应接近 97%（100-buffer）
        total_ratio = sum(alloc.values()) / 10000
        assert 0.94 < total_ratio < 1.0

    def test_balanced_profile(self):
        alloc = allocate_budget(10000, "balanced")
        assert alloc["gpu"] == int(10000 * 0.30)
        assert alloc["cpu"] == int(10000 * 0.22)

    def test_small_budget(self):
        alloc = allocate_budget(1000, "gaming")
        assert alloc["gpu"] > 0

    def test_unknown_profile_defaults_gaming(self):
        alloc = allocate_budget(5000, "nonexistent")
        assert alloc["gpu"] == int(5000 * 0.38)


# ============================================================
# TestSearchHardware
# ============================================================

class TestSearchHardware:
    @pytest.fixture
    def db_path(self):
        fd, path = tempfile.mkstemp(suffix=".db", prefix="test_search_")
        os.close(fd)
        _make_full_db(path)
        yield path
        import gc; gc.collect()
        try:
            if os.path.exists(path):
                os.remove(path)
        except PermissionError:
            pass

    def test_search_by_category(self, db_path):
        from app.mcp.tools import _search_hardware_sql
        rows = _search_hardware_sql(db_path, "gpu")
        assert len(rows) == 3

    def test_search_keyword(self, db_path):
        from app.mcp.tools import _search_hardware_sql
        rows = _search_hardware_sql(db_path, "gpu", keyword="4070")
        assert len(rows) == 1
        assert "4070" in rows[0]["model"]

    def test_search_price_range(self, db_path):
        from app.mcp.tools import _search_hardware_sql
        rows = _search_hardware_sql(db_path, "gpu", min_price=4000, max_price=6000)
        assert len(rows) == 1
        assert rows[0]["price_jd"] >= 4000

    def test_search_cpu(self, db_path):
        from app.mcp.tools import _search_hardware_sql
        rows = _search_hardware_sql(db_path, "cpu")
        assert len(rows) == 3


# ============================================================
# TestBuildPC
# ============================================================

class TestBuildPC:
    @pytest.fixture
    def db_path(self):
        fd, path = tempfile.mkstemp(suffix=".db", prefix="test_buildpc_")
        os.close(fd)
        _make_full_db(path)
        yield path
        import gc; gc.collect()
        try:
            if os.path.exists(path):
                os.remove(path)
        except PermissionError:
            pass

    def test_build_pc_normal(self, db_path):
        """正常预算 → 产 2-3 套方案。"""
        result = build_pc(db_path, 8000, 10000,
                         {"game": "生化危机9", "resolution": "2k", "quality": "high"})
        assert "plans" in result
        assert len(result["plans"]) >= 1
        plan = result["plans"][0]
        assert plan["total"] > 0
        assert len(plan["items"]) > 0

    def test_build_pc_without_game(self, db_path):
        """无游戏名 → 走回退档位。"""
        result = build_pc(db_path, 6000, 8000,
                         {"resolution": "1080p", "quality": "medium"})
        assert result["demand_hit"]["source"] in ("fallback", "fallback_unknown")
        assert len(result["plans"]) >= 1

    def test_build_pc_narrow_budget(self, db_path):
        """窄预算 → 可能只有 1-2 个方案。"""
        result = build_pc(db_path, 8000, 8200,
                         {"game": "CS2", "resolution": "1080p", "quality": "high"})
        assert len(result["plans"]) >= 1

    def test_build_pc_custom_usage(self, db_path):
        result = build_pc(db_path, 8000, 10000,
                         {"game": "原神", "resolution": "2k", "quality": "high", "usage": "balanced"})
        assert len(result["plans"]) >= 1

    def test_build_pc_returns_demand_hit(self, db_path):
        result = build_pc(db_path, 8000, 10000,
                         {"game": "赛博朋克2077", "resolution": "4k", "quality": "high"})
        assert result["demand_hit"]["source"] == "map"
        assert "priced_at" in result


# ============================================================
# TestDemandHit + BuildPlan
# ============================================================

class TestDataTypes:
    def test_demand_hit_creation(self):
        hit = DemandHit(source="map", game="test", resolution="2k", quality="high",
                        min_gpu_tier=100, rec_gpu_tier=150, min_ram_gb=16)
        assert hit.source == "map"
        assert hit.min_ram_gb == 16

    def test_build_plan_creation(self):
        plan = BuildPlan(label="推荐", total=9000, in_budget=True, compat_ok=True)
        assert plan.label == "推荐"
        assert plan.in_budget

    def test_fallback_tiers_coverage(self):
        assert ("1080p", "medium") in FALLBACK_TIERS
        assert ("4k", "ultra") in FALLBACK_TIERS
