"""
build_pc 顶层编排 — 需求映射 → 预算分配 → 逐类选件 → 兼容修复 → 预算收敛 → 多方案输出。

D3 铁律：全部总价/预算判定在 Python 完成。返回 JSON 符合 05 §4 契约。
"""

import json
import logging
from datetime import datetime, timezone

from . import PLAN_COUNT
from .demand import lookup_demand
from .allocate import allocate_budget
from .select import select_all
from .repair import repair_build
from .converge import converge_budget

logger = logging.getLogger(__name__)


def _make_perf_note(demand, plan_label: str) -> str:
    """生成方案性能说明文字。"""
    parts = []
    if demand.game and demand.game != "__generic__":
        parts.append(f"{demand.game}")
    if demand.resolution and demand.quality:
        parts.append(f"{demand.resolution} {demand.quality}画质")
    if plan_label:
        parts.append(f"({plan_label})")
    if demand.note:
        parts.append(f"— {demand.note}")
    return " ".join(parts) if parts else ""


def _build_single_plan(
    db_path: str,
    target_budget: int,
    budget_min: int,
    budget_max: int,
    demand,
    label: str,
    profile: str = "gaming",
) -> dict:
    """为单个预算目标生成一套方案。

    Returns:
        plan dict 或 None（选不到核心件）。
    """
    allocation = allocate_budget(target_budget, profile)
    sel = select_all(db_path, target_budget, allocation, demand)

    if not sel["ok"]:
        return {
            "label": label,
            "items": [],
            "total": 0,
            "in_budget": False,
            "compat_ok": False,
            "est_power": 0,
            "perf_note": "无法生成方案: " + "; ".join(sel.get("violations", ["核心件缺失"])),
            "issues": [],
        }

    items = sel["items"]

    # 兼容修复
    items, issues = repair_build(db_path, items)

    # 预算收敛
    items = converge_budget(db_path, items, budget_min, budget_max)

    # 最终验证
    from app.compat.validate import load_build_items, run_all_checks
    build = load_build_items(db_path, items)
    result = run_all_checks(build)

    total = sum(it.get("price", 0) for it in items)
    in_budget = budget_min <= total <= budget_max

    return {
        "label": label,
        "items": items,
        "total": total,
        "in_budget": in_budget,
        "compat_ok": result.compat_ok,
        "est_power": round(result.est_power, 1) if result.est_power else 0,
        "psu_headroom": round(result.psu_headroom, 2) if result.psu_headroom else None,
        "perf_note": _make_perf_note(demand, label),
        "issues": [iss.as_dict() for iss in result.issues],
    }


def build_pc(
    db_path: str,
    budget_min: int,
    budget_max: int,
    goal: dict | None = None,
    exclude: list | None = None,
) -> dict:
    """按预算与需求生成 2-3 套装机配置方案。

    Args:
        db_path: SQLite 数据库路径。
        budget_min: 预算下限（元）。
        budget_max: 预算上限（元）。
        goal: 需求 dict {game?, resolution?, quality?, fps_target?, usage?, prefer?}。
        exclude: 排除品类列表（如 ["monitor","peripherals"]）。

    Returns:
        符合 05 §4 契约的 JSON dict:
        {
          "plans": [{label, items[], total, in_budget, compat_ok, est_power, perf_note}],
          "demand_hit": {source, game, resolution, quality, ...},
          "priced_at": "ISO8601"
        }
    """
    goal = goal or {}
    game = goal.get("game")
    resolution = goal.get("resolution", "1080p")
    quality = goal.get("quality", "medium")

    # 4K 游戏回绝：当前没有显卡能原生 4K 中高画质流畅运行
    usage = goal.get("usage", "gaming")
    if resolution == "4k" and usage not in ("office", "productivity"):
        return {
            "plans": [],
            "demand_hit": {
                "source": "4k_refused",
                "game": game or "",
                "resolution": resolution,
                "quality": quality,
                "min_gpu_tier": None,
                "rec_gpu_tier": None,
                "min_cpu_tier": None,
                "rec_cpu_tier": None,
                "min_vram_gb": None,
                "min_ram_gb": None,
                "note": "4K 原生渲染目前不可行",
            },
            "priced_at": datetime.now(timezone.utc).isoformat(),
            "4k_refused": True,
            "refusal_message": (
                "亲，目前市面上的显卡还无法在原生4K分辨率下以中高画质流畅运行大型3A游戏。\n\n"
                "建议您以原生2K分辨率配置电脑，这是当前硬件条件下的甜点分辨率，"
                "可以在高画质下享受流畅的游戏体验。\n\n"
                "不过，如果您确实需要4K输出，这套2K配置也可以通过 DLSS/FSR 插帧技术"
                "和分辨率缩放来满足4K显示器的游戏运行需求——"
                "虽然不是原生4K渲染，但实际观感已经非常接近了。\n\n"
                "请告诉我您在2K分辨率下的预算和游戏需求，我来为您配置一套最适合的电脑吧！"
            ),
        }

    # 需求映射
    demand = lookup_demand(db_path, game, resolution, quality)

    # 确定 budget profile（usage 已在 4K 检测前提取）
    profile = "balanced" if usage in ("office", "productivity", "balanced") else "gaming"

    # 预算跨度
    span = budget_max - budget_min

    # 生成 2-3 个方案
    plans = []
    if span <= 500:
        # 预算窄：只出 1-2 个方案
        targets = [budget_min, budget_max]
    else:
        mid = (budget_min + budget_max) // 2
        targets = [budget_min, mid, budget_max]

    labels = ["够用", "推荐", "拉满"]
    if len(targets) == 2:
        labels = ["够用", "推荐"]

    for i, target in enumerate(targets[:PLAN_COUNT]):
        label = labels[i] if i < len(labels) else f"方案{i+1}"
        plan = _build_single_plan(db_path, target, budget_min, budget_max, demand, label, profile)
        plans.append(plan)

    # 去重（label 相同的合并）
    seen_labels = set()
    unique_plans = []
    for p in plans:
        if p["label"] not in seen_labels and p["items"]:
            seen_labels.add(p["label"])
            unique_plans.append(p)
        elif p["label"] in seen_labels:
            # 同名方案但不同预算点 → 保留第一个
            pass
        else:
            unique_plans.append(p)

    return {
        "plans": unique_plans,
        "demand_hit": {
            "source": demand.source,
            "game": demand.game,
            "resolution": demand.resolution,
            "quality": demand.quality,
            "min_gpu_tier": demand.min_gpu_tier,
            "rec_gpu_tier": demand.rec_gpu_tier,
            "min_cpu_tier": demand.min_cpu_tier,
            "rec_cpu_tier": demand.rec_cpu_tier,
            "min_vram_gb": demand.min_vram_gb,
            "min_ram_gb": demand.min_ram_gb,
            "note": demand.note,
        },
        "priced_at": datetime.now(timezone.utc).isoformat(),
    }
