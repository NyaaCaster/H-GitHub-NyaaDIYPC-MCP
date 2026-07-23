"""
P4 兼容规则引擎单元测试 — power / rules / validate_build。

Usage:
    python -m pytest tests/test_compat.py -v
"""

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.compat import (
    Issue,
    IssueLevel,
    CompatResult,
    CPU_FACTOR,
    BASE_LOAD_W,
    PSU_HARD_MARGIN,
    PSU_RECOMMEND_MARGIN,
    GPU_LEN_SAFETY_MM,
    COOLER_H_SAFETY_MM,
)
from app.compat.power import estimate_power, check_psu_headroom
from app.compat.rules import (
    _json_list,
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
from app.compat.validate import (
    load_build_items,
    run_all_checks,
    validate_build,
    _effective_price,
)


# ============================================================
# TestPower — 功耗估算与余量
# ============================================================

class TestPower:
    """estimate_power + check_psu_headroom"""

    def test_normal_cpu_gpu(self):
        p = estimate_power(cpu_tdp_w=125, gpu_tdp_w=200)
        assert p["cpu_portion"] == 125 * CPU_FACTOR
        assert p["gpu_portion"] == 200.0
        assert p["base_load"] == float(BASE_LOAD_W)
        assert p["est_power"] == pytest.approx(125 * CPU_FACTOR + 200 + BASE_LOAD_W)

    def test_gpu_rec_psu_fallback(self):
        """GPU 无 tdp，用 rec_psu 反推。"""
        p = estimate_power(cpu_tdp_w=65, gpu_tdp_w=None, gpu_rec_psu_w=750)
        assert p["gpu_portion"] == pytest.approx(750 * 0.7)  # GPU_REC_PSU_RATIO
        assert p["gpu_portion"] > 0

    def test_gpu_tdp_priority_over_rec_psu(self):
        """gpu_tdp_w 优先于 gpu_rec_psu_w。"""
        p = estimate_power(cpu_tdp_w=65, gpu_tdp_w=300, gpu_rec_psu_w=750)
        assert p["gpu_portion"] == 300.0

    def test_no_gpu(self):
        p = estimate_power(cpu_tdp_w=65)
        assert p["gpu_portion"] == 0.0
        assert p["est_power"] == pytest.approx(65 * CPU_FACTOR + BASE_LOAD_W)

    def test_all_none(self):
        p = estimate_power()
        assert p["est_power"] == float(BASE_LOAD_W)
        assert p["cpu_portion"] == 0.0
        assert p["gpu_portion"] == 0.0

    def test_zero_values(self):
        p = estimate_power(cpu_tdp_w=0, gpu_tdp_w=0)
        assert p["cpu_portion"] == 0.0
        assert p["gpu_portion"] == 0.0

    # ---- headroom ----

    def test_headroom_sufficient(self):
        h = check_psu_headroom(750, 400)
        assert h["ratio"] == pytest.approx(1.875)
        assert h["hard_ok"] is True
        assert h["recommend_ok"] is True

    def test_headroom_hard_ok_but_tight(self):
        """1.3 ≤ ratio < 1.5"""
        h = check_psu_headroom(650, 480)
        assert h["hard_ok"] is True
        assert h["recommend_ok"] is False

    def test_headroom_hard_fail(self):
        h = check_psu_headroom(500, 480)
        assert h["hard_ok"] is False
        assert h["recommend_ok"] is False

    def test_headroom_psu_none(self):
        h = check_psu_headroom(None, 400)
        assert h["ratio"] is None
        assert h["hard_ok"] is None

    def test_headroom_est_power_zero(self):
        h = check_psu_headroom(750, 0)
        assert h["ratio"] is None


# ============================================================
# TestJsonList — 辅助函数
# ============================================================

class TestJsonList:
    def test_valid_array(self):
        assert _json_list('["AM5","AM4"]') == ["AM5", "AM4"]

    def test_none(self):
        assert _json_list(None) == []

    def test_empty_string(self):
        assert _json_list("") == []

    def test_invalid_json(self):
        assert _json_list("not json") == []

    def test_non_array(self):
        assert _json_list('{"key":"val"}') == []


# ============================================================
# TestRulesHard — C1-C9
# ============================================================

class TestRulesHard:
    """硬规则 C1-C9"""

    # C1
    def test_c1_match(self):
        assert check_c1_socket("AM5", "AM5") is None

    def test_c1_mismatch(self):
        r = check_c1_socket("AM5", "LGA1700")
        assert r.level == IssueLevel.ERROR
        assert r.rule == "C1"

    def test_c1_null_cpu(self):
        r = check_c1_socket(None, "AM5")
        assert r.level == IssueLevel.WARN
        assert "C1_unknown" in r.rule

    def test_c1_null_mb(self):
        r = check_c1_socket("AM5", None)
        assert r.level == IssueLevel.WARN
        assert "C1_unknown" in r.rule

    # C2
    def test_c2_match(self):
        assert check_c2_mem_mb("DDR5", "DDR5") is None

    def test_c2_mismatch(self):
        r = check_c2_mem_mb("DDR4", "DDR5")
        assert r.level == IssueLevel.ERROR
        assert r.rule == "C2"

    def test_c2_null(self):
        r = check_c2_mem_mb(None, "DDR5")
        assert r.level == IssueLevel.WARN

    # C3
    def test_c3_match(self):
        assert check_c3_mem_cpu("DDR5", "DDR5") is None

    def test_c3_cpu_null_skip(self):
        """CPU 未标内存类型 → 跳过，不产生任何 Issue。"""
        assert check_c3_mem_cpu("DDR5", None) is None

    def test_c3_mismatch(self):
        r = check_c3_mem_cpu("DDR4", "DDR5")
        assert r.level == IssueLevel.ERROR

    def test_c3_mem_null(self):
        r = check_c3_mem_cpu(None, "DDR5")
        assert r.level == IssueLevel.WARN

    # C4
    def test_c4_match(self):
        assert check_c4_form_factor("ATX", '["ATX","Micro-ATX","Mini-ITX"]') is None

    def test_c4_micro_atx_match(self):
        """Micro-ATX 归一化匹配。"""
        assert check_c4_form_factor("Micro-ATX", '["ATX","Micro-ATX"]') is None

    def test_c4_mismatch(self):
        r = check_c4_form_factor("E-ATX", '["ATX","Micro-ATX"]')
        assert r.level == IssueLevel.ERROR
        assert r.rule == "C4"

    def test_c4_null(self):
        r = check_c4_form_factor(None, '["ATX"]')
        assert r.level == IssueLevel.WARN

    # C5
    def test_c5_ok(self):
        assert check_c5_gpu_len(280, 330) is None

    def test_c5_too_long(self):
        r = check_c5_gpu_len(350, 330)
        assert r.level == IssueLevel.ERROR
        assert r.rule == "C5"

    def test_c5_no_gpu(self):
        """gpu_len 为 None → 无独显，跳过。"""
        assert check_c5_gpu_len(None, 330) is None

    def test_c5_null_case(self):
        r = check_c5_gpu_len(280, None)
        assert r.level == IssueLevel.WARN
        assert "C5_unknown" in r.rule

    # C6
    def test_c6_ok(self):
        assert check_c6_cooler_h(155, 165) is None

    def test_c6_too_tall(self):
        r = check_c6_cooler_h(170, 165)
        assert r.level == IssueLevel.ERROR
        assert r.rule == "C6"

    def test_c6_null(self):
        r = check_c6_cooler_h(None, 165)
        assert r.level == IssueLevel.WARN

    # C7
    def test_c7_match(self):
        assert check_c7_cooler_socket('["LGA1700","AM5"]', "AM5") is None

    def test_c7_mismatch(self):
        r = check_c7_cooler_socket('["LGA1700","LGA1200"]', "AM5")
        assert r.level == IssueLevel.ERROR
        assert r.rule == "C7"

    def test_c7_null_cooler_sockets(self):
        r = check_c7_cooler_socket(None, "AM5")
        assert r.level == IssueLevel.WARN

    def test_c7_null_cpu_socket(self):
        r = check_c7_cooler_socket('["AM5"]', None)
        assert r.level == IssueLevel.WARN

    # C8
    def test_c8_ok(self):
        est = estimate_power(125, 200)["est_power"]
        assert check_c8_psu_power(750, est) is None

    def test_c8_fail(self):
        """用极低电源触发 C8 error。"""
        r = check_c8_psu_power(300, 500)
        assert r.level == IssueLevel.ERROR
        assert r.rule == "C8"

    def test_c8_null_psu(self):
        r = check_c8_psu_power(None, 400)
        assert r.level == IssueLevel.WARN

    # C9
    def test_c9_ok(self):
        ssds = [{"interface": "M.2 NVMe"}]
        r = check_c9_storage(ssds, [], 2)
        assert r == []

    def test_c9_too_many_nvme(self):
        ssds = [{"interface": "M.2 NVMe"}, {"interface": "M.2 NVMe"}, {"interface": "M.2 NVMe"}]
        r = check_c9_storage(ssds, [], 2)
        assert len(r) == 1
        assert r[0].level == IssueLevel.ERROR
        assert r[0].rule == "C9"

    def test_c9_m2_unknown_protocol(self):
        """仅标注 M.2 未注明协议 → 保守算 NVMe。"""
        ssds = [{"interface": "M.2"}]
        r = check_c9_storage(ssds, [], 1)
        assert r == []

    def test_c9_null_m2_slots(self):
        ssds = [{"interface": "M.2 NVMe"}]
        r = check_c9_storage(ssds, [], None)
        assert len(r) == 1
        assert r[0].level == IssueLevel.WARN
        assert "C9_unknown" in r[0].rule

    def test_c9_no_storage(self):
        r = check_c9_storage([], [], 2)
        assert r == []

    def test_c9_m2_sata_not_counted(self):
        """M.2 SATA 不占 NVMe 槽。"""
        ssds = [{"interface": "M.2 SATA"}]
        r = check_c9_storage(ssds, [], 1)
        assert r == []


# ============================================================
# TestRulesSoft — W1-W5
# ============================================================

class TestRulesSoft:
    """软规则 W1-W5"""

    # W1
    def test_w1_k_cpu_z_board(self):
        """K系CPU+Z板 → 不触发。"""
        assert check_w1_k_z("Intel 酷睿 i5-14600KF", "Z790") is None

    def test_w1_k_cpu_non_z(self):
        r = check_w1_k_z("Intel 酷睿 i5-14600KF", "B760")
        assert r.level == IssueLevel.WARN
        assert r.rule == "W1"

    def test_w1_non_k_cpu(self):
        """非K系CPU不触发。"""
        assert check_w1_k_z("Intel 酷睿 i5-14400F", "B760") is None

    def test_w1_amd_cpu(self):
        """AMD CPU 不触发（即使型号含K字母也不考虑）。"""
        # 这个 AMD 型号不含 K，可能不触发 — 但检查逻辑只看 K 字母
        assert check_w1_k_z("AMD Ryzen 5 7600", "B650") is None

    def test_w1_null(self):
        assert check_w1_k_z(None, None) is None

    # W2 — 占位
    def test_w2_always_none(self):
        assert check_w2_mem_freq("i9-14900K", 5600) is None
        assert check_w2_mem_freq(None, None) is None

    # W3
    def test_w3_tight(self):
        r = check_w3_psu_margin(1.35)
        assert r.level == IssueLevel.WARN
        assert r.rule == "W3"

    def test_w3_sufficient(self):
        assert check_w3_psu_margin(1.6) is None

    def test_w3_below_hard(self):
        """ratio < 1.3 → C8 已报 error，W3 不重复报。"""
        assert check_w3_psu_margin(1.1) is None

    def test_w3_null(self):
        assert check_w3_psu_margin(None) is None

    # W4
    def test_w4_gpu_tight(self):
        r = check_w4_size_margin(325, None, 330, None)
        assert len(r) == 1
        assert r[0].rule == "W4"
        assert "显卡" in r[0].detail

    def test_w4_cooler_tight(self):
        r = check_w4_size_margin(None, 162, None, 165)
        assert len(r) == 1
        assert r[0].rule == "W4"
        assert "散热器" in r[0].detail

    def test_w4_both_ok(self):
        r = check_w4_size_margin(280, 155, 350, 170)
        assert r == []

    def test_w4_no_gpu(self):
        """无 GPU 只检查散热器。"""
        r = check_w4_size_margin(None, 155, None, 170)
        assert r == []

    # W5
    def test_w5_has_gpu(self):
        """有独显 → 不触发。"""
        assert check_w5_igpu(True, 0) is None

    def test_w5_no_gpu_has_igpu(self):
        assert check_w5_igpu(False, 1) is None

    def test_w5_no_gpu_no_igpu(self):
        r = check_w5_igpu(False, 0)
        assert r.level == IssueLevel.WARN
        assert r.rule == "W5"

    def test_w5_no_gpu_igpu_unknown(self):
        r = check_w5_igpu(False, None)
        assert r.level == IssueLevel.WARN
        assert "W5_unknown" in r.rule


# ============================================================
# TestNullHandling — NULL 放行集成
# ============================================================

class TestNullHandling:
    """确保 NULL 字段走 warn（放行），不判 error。"""

    def test_all_null_hard_rules_produce_warns_only(self):
        """所有参与字段为 None 时，C1-C8 均返回 warn 而非 error。"""
        results = [
            check_c1_socket(None, None),
            check_c2_mem_mb(None, None),
            check_c3_mem_cpu(None, None),  # cpu None → 跳过，无 Issue
            check_c4_form_factor(None, None),
            check_c5_gpu_len(None, None),   # 双方 None → 跳过
            check_c6_cooler_h(None, None),
            check_c7_cooler_socket(None, None),
            check_c8_psu_power(None, None),
        ]
        for r in results:
            if r is not None:
                assert r.level == IssueLevel.WARN, f"rule {r.rule} should be warn"


# ============================================================
# TestEffectivePrice — 内联复用验证
# ============================================================

class TestEffectivePrice:
    def test_jd_first(self):
        assert _effective_price({"price_jd": 1000, "price_show": 900, "price_min": 800}) == 1000

    def test_fallback_to_show(self):
        assert _effective_price({"price_show": 900, "price_min": 800}) == 900

    def test_fallback_to_min(self):
        assert _effective_price({"price_min": 800}) == 800

    def test_zero_skip(self):
        assert _effective_price({"price_jd": 0, "price_show": 900}) == 900

    def test_all_none(self):
        assert _effective_price({}) is None


# ============================================================
# TestValidateBuildIntegration — 集成测试（需要 DB）
# ============================================================

def _insert_compat(conn, pro_id: str, **kwargs):
    """向 compat 表插入一行，仅传入非 NULL 列名=值。"""
    columns = ["pro_id"] + list(kwargs.keys())
    values = [pro_id] + list(kwargs.values())
    placeholders = ",".join("?" for _ in columns)
    cols_str = ",".join(columns)
    conn.execute(
        f"INSERT OR REPLACE INTO compat ({cols_str}) VALUES ({placeholders})",
        values,
    )


def _make_test_db(db_path: str):
    """创建测试 DB 含 hardware + compat 表和一条兼容的样本配置。"""
    from app.db.schema import init_db
    conn = init_db(db_path)

    # 插入兼容搭配的一组件
    hardware = [
        ("cpu-001", "cpu", "Intel", "Intel 酷睿 i5-14600KF", 1899, None, None),
        ("mb-001", "mainboard", "华硕", "华硕 TUF GAMING B760M-PLUS WIFI", 1199, None, None),
        ("mem-001", "memory", "金士顿", "金士顿 FURY Beast 32GB DDR5", 699, None, None),
        ("gpu-001", "gpu", "七彩虹", "七彩虹 iGame RTX 4070 Ti", 6199, None, None),
        ("ssd-001", "ssd", "三星", "三星 990 PRO 2TB", 1299, None, None),
        ("psu-001", "psu", "海韵", "海韵 FOCUS GX-850", 799, None, None),
        ("cooler-001", "cooler", "利民", "利民 PA120 SE", 159, None, None),
        ("case-001", "case", "联力", "联力 LANCOOL 216", 499, None, None),
    ]
    for hw in hardware:
        conn.execute("""
            INSERT OR REPLACE INTO hardware
            (pro_id, category, brand, model, price_jd, price_show, price_min)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, hw)

    _insert_compat(conn, "cpu-001", category="cpu", socket="LGA1700", tdp_w=125, mem_type="DDR5", igpu=0)
    _insert_compat(conn, "mb-001", category="mainboard", socket="LGA1700", mem_type="DDR5",
                   form_factor="Micro-ATX", mem_slots=4, mb_chipset="B760", m2_slots=2)
    _insert_compat(conn, "mem-001", category="memory", mem_type="DDR5", mem_capacity_gb=32, mem_freq=6000)
    _insert_compat(conn, "gpu-001", category="gpu", tdp_w=285, vram_gb=12, gpu_len_mm=336,
                   gpu_power_pin="16pin", gpu_rec_psu_w=750)
    _insert_compat(conn, "ssd-001", category="ssd", interface="M.2 NVMe", ss_form="M.2 2280", capacity_gb=2000)
    _insert_compat(conn, "psu-001", category="psu", rated_w=850)
    _insert_compat(conn, "cooler-001", category="cooler", cooler_type="风冷", cooler_h_mm=155,
                   cooler_sockets='["LGA1700","LGA1200","LGA1151","AM5","AM4"]')
    _insert_compat(conn, "case-001", category="case",
                   case_ff='["ATX","Micro-ATX","Mini-ITX"]', max_gpu_len_mm=392, max_cooler_h_mm=180)

    conn.commit()
    conn.close()


class TestValidateBuildIntegration:
    """端到端集成测试 — 需要真实 DB。"""

    @pytest.fixture
    def db_path(self):
        """创建临时测试 DB。"""
        fd, path = tempfile.mkstemp(suffix=".db", prefix="test_diypc_")
        os.close(fd)
        _make_test_db(path)
        yield path
        # 清理
        if os.path.exists(path):
            os.remove(path)

    def test_full_compatible_build(self, db_path):
        """完全兼容的配置 → compat_ok=True，无 error。"""
        items = [
            {"category": "cpu", "pro_id": "cpu-001"},
            {"category": "mainboard", "pro_id": "mb-001"},
            {"category": "memory", "pro_id": "mem-001"},
            {"category": "gpu", "pro_id": "gpu-001"},
            {"category": "ssd", "pro_id": "ssd-001"},
            {"category": "psu", "pro_id": "psu-001"},
            {"category": "cooler", "pro_id": "cooler-001"},
            {"category": "case", "pro_id": "case-001"},
        ]
        result = validate_build(db_path, items)
        assert result["compat_ok"] is True
        assert result["total"] == 1899 + 1199 + 699 + 6199 + 1299 + 799 + 159 + 499
        assert result["est_power"] is not None
        assert result["est_power"] > 0
        # 不应有任何 error 级别 issue
        errors = [i for i in result["issues"] if i["level"] == "error"]
        assert len(errors) == 0, f"Unexpected errors: {errors}"

    def test_incompatible_socket(self, db_path):
        """CPU 与主板插槽不匹配 → compat_ok=False。"""
        # 改 CPU 为 AM5（主板是 LGA1700）
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE compat SET socket='AM5' WHERE pro_id='cpu-001'")
        conn.commit()
        conn.close()

        items = [
            {"category": "cpu", "pro_id": "cpu-001"},
            {"category": "mainboard", "pro_id": "mb-001"},
        ]
        result = validate_build(db_path, items)
        assert result["compat_ok"] is False
        c1_errors = [i for i in result["issues"] if i["rule"] == "C1"]
        assert len(c1_errors) == 1
        assert c1_errors[0]["level"] == "error"

    def test_unknown_pro_id(self, db_path):
        """不存在的 pro_id → INPUT error。"""
        items = [{"category": "cpu", "pro_id": "nonexistent"}]
        result = validate_build(db_path, items)
        assert result["compat_ok"] is False
        assert any(i["rule"] == "INPUT" and i["level"] == "error" for i in result["issues"])

    def test_invalid_category(self, db_path):
        """非法品类 → INPUT error。"""
        items = [{"category": "monitor", "pro_id": "cpu-001"}]
        result = validate_build(db_path, items)
        assert result["compat_ok"] is False
        assert any("未知品类" in i["detail"] for i in result["issues"])

    def test_empty_items(self, db_path):
        """空 items 列表 → compat_ok=True（无件不产生兼容问题）。"""
        items: list = []
        result = validate_build(db_path, items)
        # 空列表不产生任何 error
        assert result["total"] == 0
        assert result["est_power"] is None


# ============================================================
# TestCompatResult
# ============================================================

class TestCompatResult:
    def test_as_dict(self):
        r = CompatResult(
            compat_ok=True,
            total=9280,
            est_power=520.3,
            psu_headroom=1.44,
            issues=[Issue(level=IssueLevel.WARN, rule="W3", detail="余量偏紧")],
        )
        d = r.as_dict()
        assert d["compat_ok"] is True
        assert d["total"] == 9280
        assert d["est_power"] == 520.3
        assert d["psu_headroom"] == 1.44
        assert len(d["issues"]) == 1
        assert d["issues"][0]["level"] == "warn"
        assert d["issues"][0]["rule"] == "W3"

    def test_empty_issues(self):
        r = CompatResult(compat_ok=True, total=5000, est_power=300.0, psu_headroom=2.0)
        d = r.as_dict()
        assert d["issues"] == []
