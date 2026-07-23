"""
型号归一与匹配 — ZOL 型号名 ↔ best-price 搜索词 ↔ 天梯对齐共用。

匹配算法取自 03-价格与对齐详细设计 §3。
"""

import logging
import re
from typing import Optional

from app.pricing import (
    BRAND_PREFIXES,
    CPU_PATTERNS,
    GPU_PATTERNS,
    SUFFIX_STOPWORDS,
)

logger = logging.getLogger(__name__)

# 全角→半角映射
_FULLWIDTH_MAP = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ（）【】",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz()[]",
)

# 匹配括号及其内容：全角/半角/方括号
RE_BRACKETS = re.compile(r"[（(【\[]([^）)\]\]]*)[）)】\]]")

# 型号数字核心（用于非 CPU/GPU 品类兜底提取）
RE_MODEL_NUMBER = re.compile(r"\b(\d{3,4}[a-z]*)\b", re.I)


def _strip_brand(name: str) -> str:
    """剥离厂商名前缀（大小写不敏感 + 跳过品牌名后紧跟型号数字的情况）。

    只剥离纯文本品牌名；品牌名后紧跟型号核心数字（如 i7/i9/ryzen/rtx）的情况不剥离，
    因为品牌名可能被后续 normalize 逻辑正确处理。
    """
    lower = name.lower()
    for prefix in sorted(BRAND_PREFIXES, key=len, reverse=True):
        pl = prefix.lower()
        # 品牌名必须在开头或紧随空格/标点
        if lower.startswith(pl):
            rest = lower[len(pl):].lstrip()
            # 如果品牌后紧跟的是数字/空格/标点（不是核心型号标识），则剥离
            if rest and (rest[0].isdigit() or rest[0] in ' -/·'):
                name = name[len(prefix):].strip()
                lower = name.lower()
                break
            # 如果剩余部分是空的（只有品牌名），也剥离
            elif not rest:
                name = name[len(prefix):].strip()
                lower = name.lower()
                break
    return name


def _strip_brackets(name: str) -> str:
    """去除全角/半角/方括号及其内容。"""
    return RE_BRACKETS.sub(" ", name)


def _strip_suffixes(name: str) -> str:
    """去除营销后缀 token（大小写不敏感词边界匹配）。"""
    for suffix in SUFFIX_STOPWORDS:
        pattern = re.compile(r'\b' + re.escape(suffix) + r'\b', re.I)
        name = pattern.sub(" ", name)
    return name


def _normalize_whitespace(name: str) -> str:
    """统一空白字符与连字符。"""
    # 连字符 → 空格
    name = name.replace("-", " ").replace("_", " ").replace("/", " ")
    # 多空格 → 单空格
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _extract_core(name: str, category: str) -> Optional[str]:
    """按品类提取核心型号 token。

    Args:
        name: 已归一化的型号名字符串。
        category: 品类枚举。

    Returns:
        核心型号 token（小写，空格分隔）或 None。
    """
    patterns = None
    if category == "cpu":
        patterns = CPU_PATTERNS
    elif category == "gpu":
        patterns = GPU_PATTERNS

    if patterns:
        for pat in patterns:
            m = pat.search(name)
            if m:
                token = m.group(0).strip()
                # 归一化 token 内空白
                token = re.sub(r"\s+", " ", token).lower()
                # "geforce rtx 5090" → "rtx 5090"
                if token.startswith("geforce "):
                    token = token[len("geforce "):]
                return token
        return None

    # 非 CPU/GPU 品类：提取最长数字型号
    numbers = RE_MODEL_NUMBER.findall(name)
    if numbers:
        longest = max(numbers, key=len)
        return longest.lower()
    return None


def normalize_model(name: str, category: str) -> str:
    """型号名归一化——去除干扰信息，提取可比较的核心 token 序列。

    归一流程：
      1. 全角→半角，转小写
      2. 剥离厂商名前缀
      3. 去括号及其内容
      4. 去营销后缀
      5. 统一空白与连字符
      6. 提取核心型号 token

    Args:
        name: 原始型号名（如 "七彩虹 iGame GeForce RTX 5090 D Vulcan OC 32GB"）。
        category: 品类枚举。

    Returns:
        归一化后的核心型号字符串（小写，如 "rtx 5090 d"）。

    >>> normalize_model("Intel 酷睿 i5-14600KF", "cpu")
    'i5 14600kf'
    >>> normalize_model("AMD Ryzen 9 9950X3D（游戏）", "cpu")
    'ryzen 9 9950x3d'
    >>> normalize_model("七彩虹 iGame GeForce RTX 5090 D Vulcan OC 32GB", "gpu")
    'rtx 5090 d'
    """
    if not name or not category:
        return ""

    # 0. 中文关键品牌词 → 英文（在大小写转换前，保持后续 pattern 匹配）
    _CN_TRANSLATE = {
        "锐龙": "ryzen",
        "酷睿": "core",
    }
    lower = name.lower()
    for cn, en in _CN_TRANSLATE.items():
        lower = lower.replace(cn, en)

    # 1. 全角→半角
    text = lower.translate(_FULLWIDTH_MAP)

    # 2. 剥离厂商名前缀
    text = _strip_brand(text)

    # 3. 去括号及其内容
    text = _strip_brackets(text)

    # 4. 去营销后缀
    text = _strip_suffixes(text)

    # 5. 统一空白与连字符
    text = _normalize_whitespace(text)

    # 6. 提取核心型号 token
    core = _extract_core(text, category)
    if core:
        return core

    # 兜底：返回完全归一化后的文本（去除所有干扰词后的剩余）
    return text.strip()


def match_models(
    name_a: str,
    name_b: str,
    category: str,
) -> bool:
    """判断两个型号名是否指向同一硬件。

    三级匹配（按优先级）：
      1. 归一后核心 token 完全相等 → True
      2. 归一后 token 集合包含关系 → True
      3. 以上都不中 → False

    Args:
        name_a: ZOL 型号名（含厂商/后缀）。
        name_b: best-price 或天梯型号名（可能含电商标题噪声）。
        category: 品类枚举。

    Returns:
        是否匹配。

    >>> match_models("i5-14600KF", "Intel 酷睿 i5 14600KF 盒装", "cpu")
    True
    >>> match_models("RTX 5090 D Vulcan OC", "七彩虹 RTX 5090 D 32GB", "gpu")
    True
    >>> match_models("RTX 5090", "RTX 5080", "gpu")
    False
    >>> match_models("Ryzen 9 9950X3D", "Ryzen 9 9950X", "cpu")
    False
    """
    if not name_a or not name_b or not category:
        return False

    na = normalize_model(name_a, category)
    nb = normalize_model(name_b, category)

    if not na or not nb:
        return False

    # Level 1: 归一后核心 token 完全相等
    if na == nb:
        return True

    # Level 2: token 集合包含关系（允许电商标题多出无关 token）
    toks_a = set(na.split())
    toks_b = set(nb.split())
    if not toks_a or not toks_b:
        return False

    # 产品品阶区分符一致性检查：ti/super/xt/gre/d 在不同型号中代表不同产品
    _QUALIFIERS = {"ti", "super", "xt", "gre", "d"}
    for qual in _QUALIFIERS:
        if (qual in toks_a) != (qual in toks_b):
            return False  # 一侧有品阶后缀而另一侧没有 → 不同产品

    # 较小集合必须是较大集合的子集
    if len(toks_a) <= len(toks_b):
        return toks_a.issubset(toks_b)
    else:
        return toks_b.issubset(toks_a)
