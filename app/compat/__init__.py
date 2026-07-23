"""
兼容规则引擎 — C1-C9 硬规则 + W1-W5 软规则 + 功耗估算。

纯函数层（rules.py / power.py）无 DB 依赖，编排层（validate.py）负责。
所有数值判定在服务端 Python 完成（D3 计算铁律）。
"""

import os
from dataclasses import dataclass, field
from enum import Enum


# ---- 数据类型 ----

class IssueLevel(str, Enum):
    """兼容问题级别。"""
    ERROR = "error"
    WARN = "warn"


@dataclass
class Issue:
    """单条兼容/约束检查结果。"""
    level: IssueLevel
    rule: str       # 规则编号，如 "C1" / "W3" / "C1_unknown"
    detail: str     # 人类可读说明

    def as_dict(self) -> dict:
        return {"level": self.level.value, "rule": self.rule, "detail": self.detail}


@dataclass
class CompatResult:
    """validate_build 完整输出。"""
    compat_ok: bool
    total: int | None                    # 方案总价（元），None 表示有件无价
    est_power: float | None              # 估算功耗（W）
    psu_headroom: float | None           # 电源余量比（rated_w / est_power）
    issues: list[Issue] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "compat_ok": self.compat_ok,
            "total": self.total,
            "est_power": round(self.est_power, 1) if self.est_power is not None else None,
            "psu_headroom": round(self.psu_headroom, 2) if self.psu_headroom is not None else None,
            "issues": [i.as_dict() for i in self.issues],
        }


# ---- 功耗常量（环境变量可覆盖） ----

def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


CPU_FACTOR = _env_float("CPU_FACTOR", 1.3)
BASE_LOAD_W = _env_float("BASE_LOAD_W", 80)
PSU_HARD_MARGIN = _env_float("PSU_HARD_MARGIN", 1.3)
PSU_RECOMMEND_MARGIN = _env_float("PSU_RECOMMEND_MARGIN", 1.5)
GPU_LEN_SAFETY_MM = _env_float("GPU_LEN_SAFETY_MM", 10)
COOLER_H_SAFETY_MM = _env_float("COOLER_H_SAFETY_MM", 10)

# GPU rec_psu_w 反推系数：建议电源约 70% 分配给 GPU
GPU_REC_PSU_RATIO = 0.7
