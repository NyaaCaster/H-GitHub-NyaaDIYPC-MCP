"""
预算基线分配 — 按 gaming / balanced profile 将总预算分配到各品类。

纯函数，无 DB 依赖。
"""

from . import BUDGET_PROFILE_GAMING, BUDGET_PROFILE_BALANCED

_PROFILES = {
    "gaming": BUDGET_PROFILE_GAMING,
    "balanced": BUDGET_PROFILE_BALANCED,
}


def allocate_budget(total_budget: int, profile: str = "gaming") -> dict[str, int]:
    """按预算 profile 将总预算分配到各品类。

    Args:
        total_budget: 总预算（元）。
        profile: "gaming" 或 "balanced"。

    Returns:
        {gpu, cpu, mainboard, memory, ssd, psu, cooler, case} 预算（元），
        不含 buffer。
    """
    ratios = _PROFILES.get(profile, BUDGET_PROFILE_GAMING)
    result: dict[str, int] = {}
    for cat in ["gpu", "cpu", "mainboard", "memory", "ssd", "psu", "cooler", "case"]:
        result[cat] = int(total_budget * ratios.get(cat, 0))
    return result
