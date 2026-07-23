"""
装机搭配算法模块 — 需求映射 + 预算分配 + 逐类选件 + 兼容修复 + 预算收敛。

P5 实现，按 05-需求映射与D6搭配算法详细设计.md 执行。
D3 铁律：全部数值判定在服务端 Python 完成。
"""

import json
import os
from dataclasses import dataclass, field


# ---- 环境变量常量 ----

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_json(name: str, default: dict) -> dict:
    val = os.getenv(name)
    if not val:
        return default
    try:
        return json.loads(val)
    except json.JSONDecodeError:
        return default


REPAIR_MAX_ITER = _env_int("REPAIR_MAX_ITER", 5)
CONVERGE_MAX_ITER = _env_int("CONVERGE_MAX_ITER", 6)
CANDIDATE_PRICE_SLACK = _env_float("CANDIDATE_PRICE_SLACK", 1.25)
PLAN_COUNT = _env_int("PLAN_COUNT", 3)

# 预算基线比例
BUDGET_PROFILE_GAMING = _env_json("BUDGET_PROFILE_GAMING", {
    "gpu": 0.38, "cpu": 0.18, "mainboard": 0.10,
    "memory": 0.08, "ssd": 0.08, "psu": 0.06,
    "cooler": 0.04, "case": 0.05, "buffer": 0.03,
})

BUDGET_PROFILE_BALANCED = _env_json("BUDGET_PROFILE_BALANCED", {
    "gpu": 0.30, "cpu": 0.22, "mainboard": 0.12,
    "memory": 0.10, "ssd": 0.10, "psu": 0.06,
    "cooler": 0.04, "case": 0.04, "buffer": 0.02,
})

# 台式机全品类列表
ALL_CATEGORIES = ["gpu", "cpu", "mainboard", "memory", "cooler", "psu", "case", "ssd"]


# ---- 数据类型 ----

@dataclass
class DemandHit:
    """需求映射结果。"""
    source: str          # "map" | "fallback" | "fallback_unknown"
    game: str            # 归一后游戏名
    resolution: str      # 1080p | 2k | 4k
    quality: str         # low | medium | high | ultra
    min_gpu_tier: float | None = None
    rec_gpu_tier: float | None = None
    min_cpu_tier: float | None = None
    rec_cpu_tier: float | None = None
    min_vram_gb: int | None = None
    min_ram_gb: int | None = None
    note: str = ""


@dataclass
class BuildPlan:
    """单套装机方案。"""
    label: str
    items: list[dict] = field(default_factory=list)
    total: int = 0
    in_budget: bool = False
    compat_ok: bool = True
    est_power: float = 0.0
    perf_note: str = ""
    issues: list[dict] = field(default_factory=list)


# ---- 天梯分回退常量（__generic__ 行的 fallback 值，设计文档 §1.3） ----

FALLBACK_TIERS = {
    ("1080p", "low"):    {"min_gpu": 80, "rec_gpu": 100, "min_cpu": 80, "min_vram": 8,  "min_ram": 16},
    ("1080p", "medium"): {"min_gpu": 80, "rec_gpu": 100, "min_cpu": 80, "min_vram": 8,  "min_ram": 16},
    ("1080p", "high"):   {"min_gpu": 110, "rec_gpu": 135, "min_cpu": 100, "min_vram": 10, "min_ram": 16},
    ("2k", "medium"):    {"min_gpu": 110, "rec_gpu": 135, "min_cpu": 100, "min_vram": 10, "min_ram": 16},
    ("2k", "high"):      {"min_gpu": 145, "rec_gpu": 170, "min_cpu": 120, "min_vram": 12, "min_ram": 32},
    ("2k", "ultra"):     {"min_gpu": 170, "rec_gpu": 190, "min_cpu": 140, "min_vram": 16, "min_ram": 32},
    ("4k", "high"):      {"min_gpu": 180, "rec_gpu": 200, "min_cpu": 140, "min_vram": 16, "min_ram": 32},
    ("4k", "ultra"):     {"min_gpu": 200, "rec_gpu": 220, "min_cpu": 160, "min_vram": 20, "min_ram": 32},
}
