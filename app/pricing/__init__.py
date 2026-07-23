"""
NyaaDIYPC-MCP 价格模块 — effective_price 决策 + best-price 实时补价 + 型号归一匹配。

模块结构（按 03-价格与对齐详细设计 §4）：
  effective.py  — effective_price 决策（jd > show > min > None）
  match.py      — normalize_model + 三级匹配（proId/核心token/品牌包含）
  bestprice.py  — best-price 调用 + 强制过滤层 + price_rt 填充

best_price_mcp 为可选依赖——import 失败时自动降级为纯 ZOL 价模式。
"""

import logging
import re

logger = logging.getLogger(__name__)

# ============================================================
# 整机/笔记本排除关键词（审计-1 过滤层）
# ============================================================
EXCLUDE_KEYWORDS: list[str] = [
    "整机", "主机", "台式机", "组装机", "准系统",
    "套装", "全套", "游戏本", "笔记本", "一体机",
    "工作站", "服务器", "迷你主机", "itx主机",
]

# ============================================================
# 型号名后缀/营销词表（normalize_model 去噪用）
# ============================================================
SUFFIX_STOPWORDS: list[str] = [
    # 英文营销后缀（注意：ti/super/ultra/d 是产品型号区分符，不在此列）
    "vulcan", "gaming", "oc", "trio", "suprim",
    "extreme", "master", "elite", "pro", "plus",
    "prime", "tough",
    "aorus", "eagle", "windforce", "phantom",
    "gamerock", "challenger", "fighter",
    "amp", "trinity", "gamingx", "ventus",
    "star", "metal", "legend", "dual", "turbo",
    "pulse", "nitro", "swift", "predator",
    "rog", "strix", "tuf",
    # 中文营销后缀
    "魔龙", "万图师", "超频版", "游戏", "白金版",
    "黑将", "大将", "冰龙", "烈焰战神", "战斧", "金属大师",
    "电竞之心", "电竞叛客", "铭瑄", "终结者",
    "雪豹", "天选", "巨齿鲨", "速驹",
    "太极", "幻影", "挑战者", "小雕", "雪雕",
    "迫击炮", "刀锋", "战斧导弹", "火箭筒",
    "大霜塔", "小霜塔", "冰立方", "冰神",
]

# ============================================================
# 厂商名前缀表（normalize_model 剥离用，对匹配无帮助）
# ============================================================
BRAND_PREFIXES: list[str] = [
    # CPU
    "intel", "amd", "英特尔", "超威", "酷睿", "锐龙",
    # GPU
    "nvidia", "英伟达", "geforce",
    "七彩虹", "colorful", "igame",
    "华硕", "asus",
    "微星", "msi",
    "技嘉", "gigabyte",
    "影驰", "galax", "galaxy",
    "索泰", "zotac",
    "蓝宝石", "sapphire",
    "迪兰", "憾讯", "powercolor",
    "铭瑄", "maxsun",
    "盈通", "yeston",
    "耕升", "gainward",
    "映众", "inno3d",
    "瀚铠", "his",
    "evga", "pny", "xfx", "讯景",
    "摩尔线程", "moore",
    "intel arc", "intel锐炫",
    # 主板
    "华擎", "asrock",
    "昂达", "onda",
    "映泰", "biostar",
    "七彩虹", "colorful",
    # 内存
    "金士顿", "kingston", "fury",
    "芝奇", "gskill", "g.skill",
    "海盗船", "corsair",
    "威刚", "adata", "xpg",
    "光威", "gloway",
    "英睿达", "crucial",
    "十铨", "teamgroup", "team",
    "宇瞻", "apacer",
    # 硬盘
    "三星", "samsung",
    "西数", "wd", "western",
    "希捷", "seagate",
    "铠侠", "kioxia", "东芝", "toshiba",
    "致钛", "fanxiang", "梵想",
    "海力士", "sk hynix", "hynix", "skhynix",
    # 电源
    "海韵", "seasonic",
    "振华", "superflower", "super flower",
    "长城", "greatwall", "great wall",
    "酷冷", "coolermaster", "cooler master",
    "安钛克", "antec",
    "全汉", "fsp",
    "先马", "sama",
    "鑫谷", "segotep",
    "美商海盗船", "corsair",
    # 散热
    "利民", "thermalright",
    "九州风神", "deepcool",
    "猫头鹰", "noctua",
    "雅浚", "thermalright",
    "乔思伯", "jonsbo",
    # 机箱
    "恩杰", "nzxt",
    "追风者", "phanteks",
    "联力", "lianli", "lian li",
    "分形工艺", "fractal",
]

# ============================================================
# 价格过滤阈值
# ============================================================
PRICE_RT_MIN_RATIO = 0.5   # 实时价 < effective_price * 0.5 → 串货/配件
PRICE_RT_MAX_RATIO = 2.0   # 实时价 > effective_price * 2.0 → 错配


# ============================================================
# 按品类核心型号提取正则（编译）
# ============================================================

def _compile_cpu_patterns() -> list[re.Pattern]:
    """CPU 核心型号正则模式。"""
    return [
        re.compile(r'core\s*i[3579]\s*-?\d{4,5}[a-z]*', re.I),
        re.compile(r'ryzen\s*[3579]\s*\d{3,4}[a-z0-9]*', re.I),
        re.compile(r'\br[3579]\s*\d{3,4}[a-z]*\b', re.I),
        re.compile(r'ultra\s*[3579]\s*\d{3}[a-z]*', re.I),
    ]


def _compile_gpu_patterns() -> list[re.Pattern]:
    """GPU 核心型号正则模式。"""
    return [
        # 支持 rtx 4070 / rtx 4070 ti / rtx 4070 super / rtx 4070 ti super / rtx 5090 d
        re.compile(r'rtx\s*\d{4}(?:\s*(?:ti|super|d\b))*', re.I),
        re.compile(r'rx\s*\d{4}(?:\s*(?:xt|gre))*', re.I),
        re.compile(r'arc\s*[ab]\d{3}', re.I),
        re.compile(r'gtx\s*\d{3,4}(?:\s*ti)?', re.I),
    ]


CPU_PATTERNS = _compile_cpu_patterns()
GPU_PATTERNS = _compile_gpu_patterns()
