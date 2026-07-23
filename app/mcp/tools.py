"""
MCP 工具定义 — P1-P5 渐进回填。

三个只读工具（审计整改 1：写操作永不暴露为 MCP 工具）：
  search_hardware  — ✅ P5 已就位
  build_pc         — ✅ P5 搭配算法已就位
  validate_build   — ✅ P4 兼容引擎已就位
"""

import json
import logging
import os
import sqlite3

from app.compat.validate import validate_build as validate_build_core
from app.build.build_pc import build_pc as build_pc_core

logger = logging.getLogger(__name__)

_HW_COLS = "h.pro_id, h.category, h.model, h.brand, h.price_jd, h.price_show, h.price_min, h.tier_score, h.popularity"


def _effective_price(row: dict) -> int | None:
    for k in ("price_jd", "price_show", "price_min"):
        p = row.get(k)
        if p is not None and p > 0:
            return p
    return None


def _search_hardware_sql(
    db_path: str, category: str,
    keyword: str = "", min_price: int = 0, max_price: int = 0,
    min_tier: float = 0, limit: int = 20,
) -> list[dict]:
    """search_hardware 的实际查询逻辑。"""
    clauses = [f"h.category='{category}'", "h.active=1"]
    if keyword:
        clauses.append(f"(h.model LIKE '%{keyword}%' OR h.brand LIKE '%{keyword}%')")
    if min_price > 0:
        clauses.append(f"h.price_jd >= {min_price}")
    if max_price > 0:
        clauses.append(f"h.price_jd <= {max_price}")
    if min_tier > 0:
        clauses.append(f"h.tier_score >= {min_tier}")

    where = " AND ".join(clauses)
    sql = f"SELECT {_HW_COLS} FROM hardware h WHERE {where} ORDER BY h.price_jd ASC LIMIT {limit}"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql)]
    conn.close()
    return rows


def register_all_placeholder_tools(mcp):
    """在 FastMCP 实例上注册 3 个工具。"""

    @mcp.tool()
    async def search_hardware(
        category: str, keyword: str = "",
        min_price: int = 0, max_price: int = 0,
        min_tier: float = 0, limit: int = 20,
    ) -> str:
        """查询硬件件目录（只读）。

        按品类 + 价格区间 + 关键词 + 天梯分下限过滤，返回件列表。
        价格使用 effective_price（jd > show > min）。

        Args:
            category: 品类 (cpu|mainboard|memory|gpu|hdd|ssd|psu|cooler|case)
            keyword: 型号/品牌关键词（可选）
            min_price: 最低价格（元，可选）
            max_price: 最高价格（元，可选）
            min_tier: 最低天梯分（可选）
            limit: 返回数量上限（默认 20）
        """
        db_path = os.getenv("DIYPC_DB_PATH", "/app/data/diypc.db")
        try:
            rows = _search_hardware_sql(db_path, category, keyword, min_price, max_price, min_tier, limit)
        except Exception as e:
            logger.exception("search_hardware failed")
            return json.dumps({"error": str(e)}, ensure_ascii=False)

        results = []
        for r in rows:
            price = _effective_price(r)
            results.append({
                "pro_id": r["pro_id"],
                "model": r["model"],
                "brand": r.get("brand", ""),
                "category": r["category"],
                "price": price,
                "tier_score": r.get("tier_score"),
                "popularity": r.get("popularity", 0),
            })
        return json.dumps(results, ensure_ascii=False)

    @mcp.tool()
    async def build_pc(
        budget_min: int, budget_max: int, goal: str = "{}", exclude: str = ""
    ) -> str:
        """按预算与需求生成装机配置方案（D3 计算铁律：全在服务端 Python 完成）。

        服务端流程：需求映射 → 预算分配 → 逐类选件 → 兼容修复 → 预算收敛 → 多方案输出。
        所有数值（总价/功耗/预算判定）由服务端计算，claude/LLM 禁止自行求和或改数字。

        Args:
            budget_min: 预算下限（元）
            budget_max: 预算上限（元）
            goal: 需求 JSON 字符串，如 {"game":"生化危机9","resolution":"2k","quality":"high"}
                  claude 负责从用户自然语言解析出结构化 goal。
            exclude: 排除项，逗号分隔（如 "monitor,peripherals"）

        Returns:
            JSON 字符串:
            {
              "plans": [{label, items[], total, in_budget, compat_ok, est_power, perf_note}],
              "demand_hit": {source, game, resolution, quality, ...},
              "priced_at": "ISO8601"
            }
        """
        # 解析 goal
        try:
            goal_dict = json.loads(goal) if isinstance(goal, str) else goal
        except json.JSONDecodeError:
            goal_dict = {}

        if not isinstance(goal_dict, dict):
            goal_dict = {}

        # 解析 exclude
        exclude_list = [e.strip() for e in exclude.split(",") if e.strip()] if exclude else []

        db_path = os.getenv("DIYPC_DB_PATH", "/app/data/diypc.db")
        try:
            result = build_pc_core(db_path, budget_min, budget_max, goal_dict, exclude_list)
        except Exception as e:
            logger.exception("build_pc failed")
            return json.dumps({
                "plans": [],
                "demand_hit": {"source": "error", "game": "", "resolution": "", "quality": ""},
                "error": str(e),
            }, ensure_ascii=False)

        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    async def validate_build(items: str) -> str:
        """校验一套装机配置的兼容性与功耗（D3 计算铁律：全在服务端 Python 完成）。

        对输入清单执行 9 条硬兼容规则 (C1-C9) + 5 条软约束 (W1-W5) +
        功耗估算 + 电源余量检查 + 总价核算。所有数值由服务端计算，
        claude/LLM 禁止自行求和或改数字。

        Args:
            items: 件列表 JSON 字符串，格式 [{"category":"cpu","pro_id":"xxx"}, ...]
                   有效 category: cpu|mainboard|memory|gpu|hdd|ssd|psu|cooler|case

        Returns:
            JSON 字符串:
            {
              "compat_ok": bool,       # 无 error 则 true
              "total": int|null,       # 方案总价（元）
              "est_power": float|null, # 估算功耗（W）
              "psu_headroom": float|null,  # 电源余量比
              "issues": [{"level":"error|warn","rule":"C1|W3|...","detail":"..."}]
            }
        """
        # 解析输入
        try:
            parsed = json.loads(items)
            if not isinstance(parsed, list):
                return json.dumps({
                    "compat_ok": False,
                    "total": None,
                    "est_power": None,
                    "psu_headroom": None,
                    "issues": [{"level": "error", "rule": "INPUT", "detail": "items 必须是 JSON 数组"}],
                }, ensure_ascii=False)
        except json.JSONDecodeError as e:
            return json.dumps({
                "compat_ok": False,
                "total": None,
                "est_power": None,
                "psu_headroom": None,
                "issues": [{"level": "error", "rule": "INPUT", "detail": f"JSON 解析失败: {e}"}],
            }, ensure_ascii=False)

        if not parsed:
            return json.dumps({
                "compat_ok": False,
                "total": None,
                "est_power": None,
                "psu_headroom": None,
                "issues": [{"level": "error", "rule": "INPUT", "detail": "items 数组不能为空"}],
            }, ensure_ascii=False)

        db_path = os.getenv("DIYPC_DB_PATH", "/app/data/diypc.db")
        try:
            result = validate_build_core(db_path, parsed)
        except Exception as e:
            logger.exception("validate_build failed")
            return json.dumps({
                "compat_ok": False,
                "total": None,
                "est_power": None,
                "psu_headroom": None,
                "issues": [{"level": "error", "rule": "INTERNAL", "detail": f"兼容校验异常: {e}"}],
            }, ensure_ascii=False)

        return json.dumps(result, ensure_ascii=False)
