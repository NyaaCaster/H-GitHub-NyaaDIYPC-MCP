"""
effective_price 决策 — 从硬件表三价中按优先级选出权威报价。

优先级（D4 定案）：
  1. price_jd  — ZOL 京东价（预算判定主基准）
  2. price_show — ZOL 攒机页展示价（jd 缺失候补）
  3. price_min  — ZOL 多商家最低价（最后兜底）
  4. None       — 完全无价 → 该件不可进方案

price_rt（实时淘宝价）永不参与 effective_price 计算，仅作方案展示附注。
"""

from typing import Optional


def effective_price(
    price_jd: Optional[int] = None,
    price_show: Optional[int] = None,
    price_min: Optional[int] = None,
) -> Optional[int]:
    """按优先级返回生效报价。

    Args:
        price_jd: ZOL 京东价（首选）。
        price_show: ZOL 展示价（候补）。
        price_min: ZOL 多商家最低价（兜底）。

    Returns:
        生效价格 int，或 None（不可进方案）。

    >>> effective_price(1899, 1850, 1799)
    1899
    >>> effective_price(None, 1850, 1799)
    1850
    >>> effective_price(None, None, 1799)
    1799
    >>> effective_price(None, None, None) is None
    True
    >>> effective_price(0, 1850, None)  # 0 视为无效
    1850
    """
    for p in (price_jd, price_show, price_min):
        if p is not None and p > 0:
            return p
    return None


def effective_price_from_row(row: dict) -> Optional[int]:
    """从 hardware 表行 dict 提取 effective_price。

    Args:
        row: dict 含 price_jd / price_show / price_min 键。

    Returns:
        effective_price int 或 None。
    """
    return effective_price(
        row.get("price_jd"),
        row.get("price_show"),
        row.get("price_min"),
    )
