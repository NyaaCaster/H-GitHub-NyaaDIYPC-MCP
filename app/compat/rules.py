"""
兼容校验规则 — C1-C9 硬规则 (error) + W1-W5 软规则 (warn)。

每项规则为纯函数，接收已从 DB 提取好的字段值，返回 Issue | None。
NULL 字段 → warn（不阻断方案），可选件缺失（如无 GPU）→ 跳过对应规则。
"""

import json
from typing import Optional

from . import Issue, IssueLevel, GPU_LEN_SAFETY_MM, COOLER_H_SAFETY_MM, PSU_HARD_MARGIN, PSU_RECOMMEND_MARGIN


# ---- 辅助 ----

def _json_list(s: str | None) -> list[str]:
    """安全解析 JSON 数组字符串，如 cooler_sockets / case_ff。"""
    if not s:
        return []
    try:
        val = json.loads(s)
        if isinstance(val, list):
            return [str(v) for v in val]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _null_warn(rule: str) -> Issue:
    """生成「字段缺失无法校验」的 warn Issue。"""
    return Issue(level=IssueLevel.WARN, rule=rule, detail="字段缺失无法校验")


def _safe_eq(a, b) -> bool | None:
    """安全比较：任一为 None 返回 None（无法判定）。"""
    if a is None or b is None:
        return None
    return a == b


# ============================================================
# 硬规则 C1-C9
# ============================================================

def check_c1_socket(
    cpu_socket: str | None,
    mb_socket: str | None,
) -> Optional[Issue]:
    """C1: CPU↔主板插槽匹配。"""
    if cpu_socket is None or mb_socket is None:
        return _null_warn("C1_unknown")
    if cpu_socket != mb_socket:
        return Issue(
            level=IssueLevel.ERROR,
            rule="C1",
            detail=f"CPU 插槽 {cpu_socket} 与主板插槽 {mb_socket} 不匹配",
        )
    return None


def check_c2_mem_mb(
    mem_type: str | None,
    mb_mem_type: str | None,
) -> Optional[Issue]:
    """C2: 内存类型↔主板内存类型。"""
    if mem_type is None or mb_mem_type is None:
        return _null_warn("C2_unknown")
    if mem_type != mb_mem_type:
        return Issue(
            level=IssueLevel.ERROR,
            rule="C2",
            detail=f"内存类型 {mem_type} 与主板支持 {mb_mem_type} 不匹配",
        )
    return None


def check_c3_mem_cpu(
    mem_type: str | None,
    cpu_mem_type: str | None,
) -> Optional[Issue]:
    """C3: 内存类型↔CPU 支持内存类型。

    CPU 未标注内存类型 → 跳过（不产生任何 Issue）。
    """
    if cpu_mem_type is None:
        # 设计明确：CPU 未标则跳过，不报 warn
        return None
    if mem_type is None:
        return _null_warn("C3_unknown")
    if mem_type != cpu_mem_type:
        return Issue(
            level=IssueLevel.ERROR,
            rule="C3",
            detail=f"内存类型 {mem_type} 与 CPU 支持 {cpu_mem_type} 不匹配",
        )
    return None


def check_c4_form_factor(
    mb_form_factor: str | None,
    case_ff_json: str | None,
) -> Optional[Issue]:
    """C4: 主板板型 ∈ 机箱支持板型列表。"""
    if mb_form_factor is None or case_ff_json is None:
        return _null_warn("C4_unknown")
    supported = _json_list(case_ff_json)
    if not supported:
        return _null_warn("C4_unknown")
    # 归一化比较：忽略大小写和连字符
    mb_norm = mb_form_factor.upper().replace("-", "").replace(" ", "")
    supported_norm = [s.upper().replace("-", "").replace(" ", "") for s in supported]
    if mb_norm not in supported_norm:
        return Issue(
            level=IssueLevel.ERROR,
            rule="C4",
            detail=f"主板板型 {mb_form_factor} 不在机箱支持的板型列表 [{', '.join(supported)}] 中",
        )
    return None


def check_c5_gpu_len(
    gpu_len_mm: int | None,
    case_max_gpu_len_mm: int | None,
) -> Optional[Issue]:
    """C5: 显卡长度 ≤ 机箱最大显卡限长。无 GPU → 跳过。"""
    if gpu_len_mm is None and case_max_gpu_len_mm is None:
        return None  # 双方都无值，说明可能无 GPU
    if gpu_len_mm is None:
        return None  # 无 GPU，跳过
    if case_max_gpu_len_mm is None:
        return _null_warn("C5_unknown")
    if gpu_len_mm > case_max_gpu_len_mm:
        return Issue(
            level=IssueLevel.ERROR,
            rule="C5",
            detail=f"显卡长度 {gpu_len_mm}mm 超过机箱最大限长 {case_max_gpu_len_mm}mm",
        )
    return None


def check_c6_cooler_h(
    cooler_h_mm: int | None,
    case_max_cooler_h_mm: int | None,
) -> Optional[Issue]:
    """C6: 散热器高度 ≤ 机箱最大散热器限高。"""
    if cooler_h_mm is None or case_max_cooler_h_mm is None:
        return _null_warn("C6_unknown")
    if cooler_h_mm > case_max_cooler_h_mm:
        return Issue(
            level=IssueLevel.ERROR,
            rule="C6",
            detail=f"散热器高度 {cooler_h_mm}mm 超过机箱最大限高 {case_max_cooler_h_mm}mm",
        )
    return None


def check_c7_cooler_socket(
    cooler_sockets_json: str | None,
    cpu_socket: str | None,
) -> Optional[Issue]:
    """C7: CPU 插槽 ∈ 散热器支持插槽列表。"""
    if cooler_sockets_json is None or cpu_socket is None:
        return _null_warn("C7_unknown")
    supported = _json_list(cooler_sockets_json)
    if not supported:
        return _null_warn("C7_unknown")
    # 归一化比较
    cpu_norm = cpu_socket.upper().replace(" ", "")
    supported_norm = [s.upper().replace(" ", "") for s in supported]
    if cpu_norm not in supported_norm:
        return Issue(
            level=IssueLevel.ERROR,
            rule="C7",
            detail=f"散热器不支持 CPU 插槽 {cpu_socket}（支持: {', '.join(supported)}）",
        )
    return None


def check_c8_psu_power(
    psu_rated_w: int | None,
    est_power: float | None,
) -> Optional[Issue]:
    """C8: 电源额定功率 ≥ 估算功耗 × 硬边际 (1.3)。"""
    if psu_rated_w is None:
        return _null_warn("C8_unknown")
    if est_power is None or est_power <= 0:
        return _null_warn("C8_unknown")
    required = est_power * PSU_HARD_MARGIN
    if psu_rated_w < required:
        return Issue(
            level=IssueLevel.ERROR,
            rule="C8",
            detail=f"电源额定 {psu_rated_w}W 不足，估算功耗 {est_power:.0f}W 需 ≥{required:.0f}W（×{PSU_HARD_MARGIN}）",
        )
    return None


def check_c9_storage(
    ssds: list[dict] | None,
    hdds: list[dict] | None,
    mb_m2_slots: int | None,
) -> list[Issue]:
    """C9: M.2 NVMe 件数 ≤ 主板 M.2 插槽数。

    不是单一 Issue，可能产生多个。返回 Issue 列表。
    """
    issues: list[Issue] = []

    ssds = ssds or []
    hdds = hdds or []

    # 统计 M.2 NVMe SSD 数量
    m2_nvme_count = 0
    for ssd in ssds:
        iface = (ssd.get("interface") or "").upper().replace(" ", "")
        if "M.2" in iface or "M2" in iface:
            if "NVME" in iface:
                m2_nvme_count += 1
            elif "SATA" not in iface:
                # 仅标注 M.2 未注明协议，保守算 NVMe
                m2_nvme_count += 1

    # HDD 不占 M.2 槽（SATA 接口），不计数

    if mb_m2_slots is None:
        if m2_nvme_count > 0:
            issues.append(_null_warn("C9_unknown"))
        return issues

    if m2_nvme_count > mb_m2_slots:
        issues.append(Issue(
            level=IssueLevel.ERROR,
            rule="C9",
            detail=f"M.2 NVMe SSD 数量 {m2_nvme_count} 超过主板 M.2 插槽数 {mb_m2_slots}",
        ))

    return issues


# ============================================================
# 软规则 W1-W5
# ============================================================

def check_w1_k_z(
    cpu_model: str | None,
    mb_chipset: str | None,
) -> Optional[Issue]:
    """W1: K 系 CPU 建议配 Z 系列主板。

    判定：Intel CPU 型号含 "K"（不区分大小写）且主板芯片组不以 "Z" 开头。
    """
    if cpu_model is None or mb_chipset is None:
        return None  # 不满足判定条件则跳过
    cpu_upper = cpu_model.upper()
    mb_chipset_upper = mb_chipset.upper()
    # 仅 Intel K 系触发
    if "K" in cpu_upper and not mb_chipset_upper.startswith("Z"):
        return Issue(
            level=IssueLevel.WARN,
            rule="W1",
            detail=f"K 系 CPU ({cpu_model}) 建议搭配 Z 系列主板以发挥超频能力，当前芯片组: {mb_chipset}",
        )
    return None


def check_w2_mem_freq(
    cpu_model: str | None,
    mem_freq: int | None,
) -> Optional[Issue]:
    """W2: 高端 CPU 建议配高规格内存。

    V1 占位实现 — 按设计文档 §2.2 "略"，保留接口供后续扩展。
    """
    return None


def check_w3_psu_margin(
    ratio: float | None,
) -> Optional[Issue]:
    """W3: 供电余量偏紧。

    1.3 ≤ ratio < 1.5 → 够用但不宽裕。
    """
    if ratio is None:
        return None
    if PSU_HARD_MARGIN <= ratio < PSU_RECOMMEND_MARGIN:
        return Issue(
            level=IssueLevel.WARN,
            rule="W3",
            detail=f"供电余量偏紧（{ratio:.2f}＜{PSU_RECOMMEND_MARGIN}），够用但升级空间有限",
        )
    return None


def check_w4_size_margin(
    gpu_len_mm: int | None,
    cooler_h_mm: int | None,
    case_max_gpu_len_mm: int | None,
    case_max_cooler_h_mm: int | None,
) -> list[Issue]:
    """W4: 尺寸余量偏紧。

    显卡长度 / 散热器高度与机箱上限差 < 安全裕度 → warn。
    可能产生多个 Issue。
    """
    issues: list[Issue] = []

    if gpu_len_mm is not None and case_max_gpu_len_mm is not None:
        gap = case_max_gpu_len_mm - gpu_len_mm
        if 0 <= gap < GPU_LEN_SAFETY_MM:
            issues.append(Issue(
                level=IssueLevel.WARN,
                rule="W4",
                detail=f"显卡长度余量仅 {gap}mm（显卡 {gpu_len_mm}mm / 机箱限长 {case_max_gpu_len_mm}mm），建议预留 ≥{GPU_LEN_SAFETY_MM:.0f}mm",
            ))

    if cooler_h_mm is not None and case_max_cooler_h_mm is not None:
        gap = case_max_cooler_h_mm - cooler_h_mm
        if 0 <= gap < COOLER_H_SAFETY_MM:
            issues.append(Issue(
                level=IssueLevel.WARN,
                rule="W4",
                detail=f"散热器高度余量仅 {gap}mm（散热器 {cooler_h_mm}mm / 机箱限高 {case_max_cooler_h_mm}mm），建议预留 ≥{COOLER_H_SAFETY_MM:.0f}mm",
            ))

    return issues


def check_w5_igpu(
    has_gpu: bool,
    cpu_igpu: int | None,
) -> Optional[Issue]:
    """W5: 无独显且 CPU 无核显 → 无图形输出。

    Args:
        has_gpu: 配置中是否有独立显卡。
        cpu_igpu: compat 表中的 igpu 字段（1=有核显，0=无，None=未知）。
    """
    if has_gpu:
        return None  # 有独显，无需核显
    if cpu_igpu is None:
        return Issue(
            level=IssueLevel.WARN,
            rule="W5_unknown",
            detail="无独立显卡但 CPU 核显信息未知，无法确认是否有图形输出",
        )
    if cpu_igpu == 0:
        return Issue(
            level=IssueLevel.WARN,
            rule="W5",
            detail="无独立显卡且 CPU 无核显，系统将无图形输出能力",
        )
    return None
