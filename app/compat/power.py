"""
功耗估算与电源余量检查 — 纯函数，无 DB 依赖。

D3 计算铁律：全部数值判定在服务端 Python 完成。
"""

from . import CPU_FACTOR, BASE_LOAD_W, PSU_HARD_MARGIN, PSU_RECOMMEND_MARGIN, GPU_REC_PSU_RATIO


def estimate_power(
    cpu_tdp_w: int | None = None,
    gpu_tdp_w: int | None = None,
    gpu_rec_psu_w: int | None = None,
) -> dict:
    """估算整机功耗。

    Args:
        cpu_tdp_w: CPU 热设计功耗（W），None 时该分量为 0。
        gpu_tdp_w: GPU 热设计功耗（W），优先使用。
        gpu_rec_psu_w: GPU 建议电源功率（W），gpu_tdp_w 缺失时以此反推。

    Returns:
        {
            "est_power": float,       # 估算总功耗
            "cpu_portion": float,     # CPU 部分（含系数）
            "gpu_portion": float,     # GPU 部分
            "base_load": float,       # 基础负载
        }
    """
    cpu_part = (cpu_tdp_w or 0) * CPU_FACTOR

    gpu_part = 0.0
    if gpu_tdp_w is not None and gpu_tdp_w > 0:
        gpu_part = float(gpu_tdp_w)
    elif gpu_rec_psu_w is not None and gpu_rec_psu_w > 0:
        # 建议电源约 70% 分配给 GPU，用整机建议反推 GPU 功耗
        gpu_part = float(gpu_rec_psu_w) * GPU_REC_PSU_RATIO

    base = float(BASE_LOAD_W)
    total = cpu_part + gpu_part + base

    return {
        "est_power": total,
        "cpu_portion": cpu_part,
        "gpu_portion": gpu_part,
        "base_load": base,
    }


def check_psu_headroom(
    psu_rated_w: int | None,
    est_power: float | None,
) -> dict:
    """检查电源额定功率相对估算功耗的余量。

    Args:
        psu_rated_w: 电源额定功率（W），None 时 hard_ok/recommend_ok 均为 None。
        est_power: 估算功耗（W），None 或 ≤0 时 ratio 返回 None。

    Returns:
        {
            "ratio": float | None,       # psu_rated_w / est_power
            "hard_ok": bool | None,      # C8 硬约束：ratio >= 1.3
            "recommend_ok": bool | None, # 推荐目标：ratio >= 1.5
        }
    """
    if psu_rated_w is None or psu_rated_w <= 0:
        return {"ratio": None, "hard_ok": None, "recommend_ok": None}
    if est_power is None or est_power <= 0:
        return {"ratio": None, "hard_ok": None, "recommend_ok": None}

    ratio = psu_rated_w / est_power
    return {
        "ratio": ratio,
        "hard_ok": ratio >= PSU_HARD_MARGIN,
        "recommend_ok": ratio >= PSU_RECOMMEND_MARGIN,
    }
