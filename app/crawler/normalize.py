"""
specs → compat 归一映射 — 中文规格字段 → compat 表结构化列。

每个品类一个归一函数，从原始中文字段值中提取标准化兼容键。
取值规则取自 01-爬虫模块详细设计 §4.3 和 02-数据模型详细设计 §2。

缺失字段返回 None（兼容校验按「未知→放行+告警」处理）。
"""

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---- 通用提取工具 ----

RE_NUMBER = re.compile(r"(\d+\.?\d*)")
RE_INT = re.compile(r"(\d+)")


def _extract_int(text: Optional[str]) -> Optional[int]:
    """从文本中提取第一个整数。"""
    if not text:
        return None
    m = RE_INT.search(text)
    return int(m.group(1)) if m else None


def _extract_float(text: Optional[str]) -> Optional[float]:
    """从文本中提取第一个数字（含小数）。"""
    if not text:
        return None
    m = RE_NUMBER.search(text)
    return float(m.group(1)) if m else None


def _extract_mm(text: Optional[str]) -> Optional[int]:
    """从文本中提取长度 mm 数值。「336mm」→ 336，「336」→ 336。"""
    if not text:
        return None
    # 「约336×140×55mm」取第一个数字
    return _extract_int(text)


def _extract_w(text: Optional[str]) -> Optional[int]:
    """从文本中提取功率 W 数值。「125W」→ 125，「750W」→ 750。"""
    if not text:
        return None
    return _extract_int(text)


def _extract_gb(text: Optional[str]) -> Optional[int]:
    """从文本中提取容量 GB 数值。「32GB」→ 32，「2TB」→ 2000，「500GB」→ 500。"""
    if not text:
        return None
    text_upper = text.upper()
    m = RE_NUMBER.search(text_upper)
    if not m:
        return None
    val = float(m.group(1))
    if "TB" in text_upper:
        val *= 1024
    return int(val)


# ---- 插槽归一表 ----

_SOCKET_MAP: dict[str, str] = {}
for _s in [
    "LGA1700", "LGA1851", "LGA1200", "LGA1151", "LGA1150", "LGA2066",
    "AM5", "AM4", "AM3+", "AM3", "TR5", "sTRX4", "sTR4",
]:
    _SOCKET_MAP[_s.upper()] = _s
    _SOCKET_MAP[_s.lower()] = _s


def _normalize_socket(raw: Optional[str]) -> Optional[str]:
    """归一化插槽名称。「Socket AM5」→「AM5」,「LGA 1851」→「LGA1851」。"""
    if not raw:
        return None
    upper = raw.upper().replace("SOCKET", "").replace(" ", "").strip()
    return _SOCKET_MAP.get(upper, upper.title() if upper else None)


# ---- 板型归一表 ----

_FORM_FACTOR_MAP = {
    "ATX": "ATX", "MICROATX": "Micro-ATX", "MINIITX": "Mini-ITX",
    "MINI-ITX": "Mini-ITX", "EATX": "E-ATX", "XL-ATX": "XL-ATX",
}


def _normalize_form_factor(raw: Optional[str]) -> Optional[str]:
    """归一化板型。「Micro ATX板型」→「Micro-ATX」。"""
    if not raw:
        return None
    upper = raw.upper().replace(" ", "").replace("板型", "").replace("-", "").strip()
    for key, val in _FORM_FACTOR_MAP.items():
        if key.replace("-", "").replace(" ", "") == upper:
            return val
    # 部分匹配
    if "ATX" in upper and "MICRO" not in upper and "MINI" not in upper:
        return "ATX"
    if "MICRO" in upper:
        return "Micro-ATX"
    if "MINI" in upper:
        return "Mini-ITX"
    return raw.strip()


# ---- 内存类型归一 ----

def _normalize_mem_type(raw: Optional[str]) -> Optional[str]:
    """「DDR5」「2×DDR5 DIMM」→「DDR5」。"""
    if not raw:
        return None
    upper = raw.upper().replace(" ", "")
    if "DDR5" in upper:
        return "DDR5"
    if "DDR4" in upper:
        return "DDR4"
    if "DDR3" in upper:
        return "DDR3"
    return None


# ---- 接口类型归一 ----

def _normalize_interface(raw: Optional[str]) -> Optional[str]:
    """「M.2 PCIe接口」「SATA3」「M.2 NVMe」→ 统一标签。"""
    if not raw:
        return None
    upper = raw.upper().replace(" ", "").replace("接口", "").replace("协议", "")
    if "M.2" in upper or "M2" in upper:
        if "NVME" in upper:
            return "M.2 NVMe"
        if "SATA" in upper:
            return "M.2 SATA"
        return "M.2"
    if "SATA" in upper:
        if "3" in upper:
            return "SATA3"
        return "SATA"
    return raw.strip()


# ---- 80PLUS 归一 ----

_CERT_MAP = {
    "钛金": "Titanium", "白金": "Platinum", "金牌": "Gold",
    "银牌": "Silver", "铜牌": "Bronze", "白牌": "White",
}


def _normalize_cert(raw: Optional[str]) -> Optional[str]:
    """「金牌」「80PLUS金牌」→「Gold」。"""
    if not raw:
        return None
    for cn, en in _CERT_MAP.items():
        if cn in raw:
            return en
    return raw.strip()


# ---- 模组归一 ----

_MODULAR_MAP = {
    "全模组": "全模组", "半模组": "半模组",
    "非模组": "非模组", "全模": "全模组", "半模": "半模组",
}


def _normalize_modular(raw: Optional[str]) -> Optional[str]:
    """「全模组电源」「半模组」→「全模组」/「半模组」/「非模组」。"""
    if not raw:
        return None
    for key, val in _MODULAR_MAP.items():
        if key in raw:
            return val
    return raw.strip()


# ---- 散热类型归一 ----

def _normalize_cooler_type(raw: Optional[str]) -> Optional[str]:
    """「风冷，热管」「水冷」→「风冷」/「水冷」。"""
    if not raw:
        return None
    if "水冷" in raw or "液冷" in raw:
        return "水冷"
    if "风冷" in raw or "热管" in raw or "散热片" in raw:
        return "风冷"
    return raw.strip()


# ---- 解析插座列表（散热器适用范围 / 机箱板型列表） ----

def _parse_socket_list(raw: Optional[str]) -> Optional[str]:
    """「Intel：LGA 115X/1200/1700；AMD：AM4/AM5」→ JSON 数组字符串。

    返回 JSON 字符串如 '["LGA115X","LGA1200","LGA1700","AM4","AM5"]'。
    """
    if not raw:
        return None
    # 匹配所有可能的插槽标识
    sockets: list[str] = []
    # LGA 系列
    for m in re.finditer(r"LGA\s*\d+[A-Za-z]*", raw, re.I):
        s = m.group(0).upper().replace(" ", "")
        if s == "LGA115X":
            # 展开为完整列表
            sockets.extend(["LGA1150", "LGA1151", "LGA1155", "LGA1156"])
        else:
            sockets.append(s)
    # AM 系列
    for m in re.finditer(r"AM\d\+?", raw, re.I):
        s = m.group(0).upper().replace(" ", "")
        if s == "AM4":
            sockets.append("AM4")
        elif s == "AM5":
            sockets.append("AM5")
        elif s == "AM3+":
            sockets.append("AM3+")
        elif s == "AM3":
            sockets.append("AM3")
    # 去重保持顺序
    seen: set[str] = set()
    unique = [s for s in sockets if not (s in seen or seen.add(s))]
    return json.dumps(unique) if unique else None


def _parse_form_factor_list(raw: Optional[str]) -> Optional[str]:
    """「ATX/M-ATX/ITX」→ JSON 数组字符串 '["ATX","Micro-ATX","Mini-ITX"]'。"""
    if not raw:
        return None
    results: list[str] = []
    upper = raw.upper().replace(" ", "").replace("板型", "")
    if "ATX" in upper:
        if "MICRO" in upper or "MATX" in upper or "M-ATX" in upper:
            results.append("Micro-ATX")
        elif "MINI" in upper:
            results.append("Mini-ITX")
        elif "E-ATX" in upper or "EATX" in upper:
            results.append("E-ATX")
        else:
            results.append("ATX")
    # 再试分割
    parts = re.split(r"[/,;、]", raw)
    for part in parts:
        nf = _normalize_form_factor(part.strip())
        if nf and nf not in results:
            results.append(nf)
    return json.dumps(results) if results else None


# ============================================================
# 各品类归一函数
# ============================================================

def _normalize_cpu(embed: dict, deep: dict) -> dict:
    """CPU compat 归一。"""
    merged = {**embed, **deep}
    socket_raw = merged.get("插槽类型", "")
    return {
        "category": "cpu",
        "socket": _normalize_socket(socket_raw),
        "tdp_w": _extract_w(merged.get("热设计功耗(TDP)")),
        "mem_type": _normalize_mem_type(merged.get("内存类型")),
        "igpu": None,  # 需额外数据源，暂不填充
    }


def _normalize_mainboard(embed: dict, deep: dict) -> dict:
    """主板 compat 归一。"""
    merged = {**embed, **deep}
    return {
        "category": "mainboard",
        "socket": _normalize_socket(merged.get("CPU插槽")),
        "form_factor": _normalize_form_factor(merged.get("主板板型")),
        "mem_type": _normalize_mem_type(merged.get("内存类型")),
        "mem_slots": _extract_int(merged.get("内存插槽")),
        "mb_chipset": merged.get("主芯片组", "").strip() or None,
        "m2_slots": None,  # M.2 插槽数需额外解析
    }


def _normalize_memory(embed: dict, deep: dict) -> dict:
    """内存 compat 归一。"""
    merged = {**embed, **deep}
    capacity_desc = merged.get("容量描述", "")
    return {
        "category": "memory",
        "mem_type": _normalize_mem_type(merged.get("内存类型")),
        "mem_capacity_gb": _extract_gb(capacity_desc),
        "mem_freq": _extract_int(merged.get("内存主频")),
    }


def _normalize_gpu(embed: dict, deep: dict) -> dict:
    """显卡 compat 归一。"""
    merged = {**embed, **deep}
    vram_raw = merged.get("显存容量", "")
    return {
        "category": "gpu",
        "vram_gb": _extract_gb(vram_raw),
        "gpu_len_mm": _extract_mm(merged.get("显卡长度")),
        "gpu_power_pin": merged.get("电源接口", "").strip() or None,
        "gpu_rec_psu_w": _extract_w(merged.get("建议电源")),
        "tdp_w": _extract_w(merged.get("热设计功耗(TDP)")),
    }


def _normalize_hdd(embed: dict, deep: dict) -> dict:
    """机械硬盘 compat 归一。"""
    merged = {**embed, **deep}
    return {
        "category": "hdd",
        "interface": _normalize_interface(merged.get("接口类型")),
        "size_inch": _extract_float(merged.get("硬盘尺寸")),
        "capacity_gb": _extract_gb(merged.get("硬盘容量")),
    }


def _normalize_ssd(embed: dict, deep: dict) -> dict:
    """固态硬盘 compat 归一。"""
    merged = {**embed, **deep}
    return {
        "category": "ssd",
        "interface": _normalize_interface(merged.get("接口类型")),
        "ss_form": merged.get("外形尺寸", "").strip() or None,
        "capacity_gb": _extract_gb(merged.get("存储容量")),
    }


def _normalize_psu(embed: dict, deep: dict) -> dict:
    """电源 compat 归一。"""
    merged = {**embed, **deep}
    return {
        "category": "psu",
        "rated_w": _extract_w(merged.get("额定功率")),
        "modular": _normalize_modular(merged.get("电源模组") or merged.get("模组类型")),
        "cert": _normalize_cert(merged.get("80PLUS认证")),
    }


def _normalize_cooler(embed: dict, deep: dict) -> dict:
    """散热器 compat 归一。"""
    merged = {**embed, **deep}
    scope = merged.get("适用范围", "")
    return {
        "category": "cooler",
        "cooler_type": _normalize_cooler_type(
            merged.get("散热器类型") or merged.get("散热方式")
        ),
        "cooler_h_mm": _extract_mm(merged.get("散热器高度")),
        "cooler_sockets": _parse_socket_list(scope),
    }


def _normalize_case(embed: dict, deep: dict) -> dict:
    """机箱 compat 归一。"""
    merged = {**embed, **deep}
    structure = merged.get("机箱结构", "")
    return {
        "category": "case",
        "case_ff": _parse_form_factor_list(structure),
        "max_gpu_len_mm": _extract_mm(merged.get("最大显卡长度")),
        "max_cooler_h_mm": _extract_mm(merged.get("最大散热器高度")),
        "radiator_support": None,  # V1 暂不解析水冷排位
    }


# 品类 → 归一函数映射
_NORMALIZERS = {
    "cpu": _normalize_cpu,
    "mainboard": _normalize_mainboard,
    "memory": _normalize_memory,
    "gpu": _normalize_gpu,
    "hdd": _normalize_hdd,
    "ssd": _normalize_ssd,
    "psu": _normalize_psu,
    "cooler": _normalize_cooler,
    "case": _normalize_case,
}


def normalize_compat(
    category: str,
    embed_params: dict[str, str],
    deep_params: dict[str, str],
) -> dict[str, Any]:
    """将内嵌参数 + 深参合并后归一化为 compat 表字段 dict。

    Args:
        category: 品类枚举（cpu/mainboard/.../case）。
        embed_params: GetGoods 内嵌参数字典。
        deep_params: param.shtml 深参字典（可为空）。

    Returns:
        compat dict，含 category + 各品类专属列。缺失字段为 None。
    """
    normalizer = _NORMALIZERS.get(category)
    if not normalizer:
        logger.warning("No normalizer for category=%s, returning empty compat", category)
        return {"category": category}

    result = normalizer(embed_params, deep_params)
    # 确保 category 存在
    result["category"] = category
    return result
