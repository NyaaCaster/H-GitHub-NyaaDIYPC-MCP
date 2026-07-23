"""
NyaaDIYPC-MCP 爬虫模块 — ZOL 9 品类硬件数据抓取。

模块结构（按 01-爬虫模块详细设计 §9）：
  fetch.py      — HTTP 会话（Referer/UA/重试/限速/GBK 解码）
  getgoods.py   — GetGoods 接口 + 条目解析
  parampage.py  — 深参页解析
  tianti.py     — CPU/显卡天梯解析
  normalize.py  — specs → compat 归一
  store.py      — 幂等 UPSERT 落库
  run.py        — 编排 + cron 入口

爬虫不入 MCP 工具面（审计整改 1）。写 DB 前必须备份 .db。
"""

import logging

logger = logging.getLogger(__name__)

# ============================================================
# 9 大品类元数据（设计文档 §2.3 + §3.3 + §4.3）
# ============================================================
# 每品类定义：
#   subcate_id   — ZOL GetGoods 品类 ID
#   prefix       — 详情 URL 路径前缀
#   embed_fields — 列表内嵌 5 字段名（用于 GetGoods 条目解析）
#   deep_fields  — 深参必取字段名列表（用于 compat 归一 + 深参过滤）
# ============================================================

CATEGORIES: dict[str, dict] = {
    "cpu": {
        "subcate_id": 28,
        "prefix": "/cpu/",
        "detail_prefix": "cpu",
        "embed_fields": ["插槽类型", "CPU主频", "加速频率", "核心数量", "制作工艺"],
        "deep_fields": [
            "插槽类型",
            "热设计功耗(TDP)",
            "内存类型",
        ],
    },
    "mainboard": {
        "subcate_id": 5,
        "prefix": "/motherboard/",
        "detail_prefix": "motherboard",
        "embed_fields": ["主芯片组", "CPU插槽", "主板板型", "集成芯片", "内存类型"],
        "deep_fields": [
            "CPU插槽",
            "主板板型",
            "内存类型",
            "内存插槽",
            "最大内存容量",
        ],
    },
    "memory": {
        "subcate_id": 3,
        "prefix": "/memory/",
        "detail_prefix": "memory",
        "embed_fields": ["容量描述", "内存类型", "内存主频", "CL延迟", "适用类型"],
        "deep_fields": [
            "内存类型",
            "容量描述",
            "内存主频",
        ],
    },
    "gpu": {
        "subcate_id": 6,
        "prefix": "/vga/",
        "detail_prefix": "vga",
        "embed_fields": ["芯片厂商", "显卡芯片", "显存容量", "显存位宽", "I/O接口"],
        "deep_fields": [
            "显卡芯片",
            "显存容量",
            "显卡长度",
            "电源接口",
            "建议电源",
            "热设计功耗(TDP)",
        ],
    },
    "hdd": {
        "subcate_id": 2,
        "prefix": "/hard_drives/",
        "detail_prefix": "hard_drives",
        "embed_fields": ["硬盘容量", "接口类型", "转速", "缓存", "硬盘尺寸"],
        "deep_fields": [
            "硬盘容量",
            "接口类型",
            "转速",
            "硬盘尺寸",
        ],
    },
    "ssd": {
        "subcate_id": 626,
        "prefix": "/solid_state_drive/",
        "detail_prefix": "solid_state_drive",
        "embed_fields": ["存储容量", "接口类型", "读取速度", "缓存", "外形尺寸"],
        "deep_fields": [
            "接口类型",
            "外形尺寸",
            "存储容量",
        ],
    },
    "psu": {
        "subcate_id": 35,
        "prefix": "/power/",
        "detail_prefix": "power",
        "embed_fields": ["额定功率", "PFC类型", "80PLUS认证", "主板接口", "电源模组"],
        "deep_fields": [
            "额定功率",
            "电源模组",
            "80PLUS认证",
            "电源尺寸",
        ],
    },
    "cooler": {
        "subcate_id": 67,
        "prefix": "/cooling_product/",
        "detail_prefix": "cooling_product",
        "embed_fields": ["散热器类型", "散热方式", "适用范围", "最大风量", "轴承类型"],
        "deep_fields": [
            "适用范围",
            "散热方式",
            "散热器高度",
            "散热器类型",
        ],
    },
    "case": {
        "subcate_id": 10,
        "prefix": "/case/",
        "detail_prefix": "case",
        "embed_fields": ["机箱类型", "机箱结构", "USB接口", "3.5英寸仓位", "机箱材质"],
        "deep_fields": [
            "机箱结构",
            "最大显卡长度",
            "最大散热器高度",
            "机箱类型",
        ],
    },
}

# 品类列表（按定义顺序）
CATEGORY_NAMES = list(CATEGORIES.keys())

# 有天梯数据的品类
TIER_CATEGORIES = {"cpu", "gpu"}


def get_subcate_id(category: str) -> int:
    """安全获取 subcate_id；未知品类返回 -1。"""
    return CATEGORIES.get(category, {}).get("subcate_id", -1)


def get_deep_fields(category: str) -> list[str]:
    """获取某品类深参必取字段名列表。"""
    return CATEGORIES.get(category, {}).get("deep_fields", [])


# 模块加载时打印品类汇总
_category_summary = ", ".join(
    f"{k}({v['subcate_id']})" for k, v in CATEGORIES.items()
)
logger.info("9 categories loaded: %s", _category_summary)
