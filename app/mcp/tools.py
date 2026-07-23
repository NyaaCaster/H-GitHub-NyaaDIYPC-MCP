"""
MCP 工具定义 — P1-P4 渐进回填。

三个只读工具（审计整改 1：写操作永不暴露为 MCP 工具）：
  search_hardware  — P2-P3 爬虫+价格模块就位后回填
  build_pc         — P5 搭配算法就位后回填
  validate_build   — ✅ P4 兼容引擎已就位
"""

import json
import logging
import os

from app.compat.validate import validate_build as validate_build_core

logger = logging.getLogger(__name__)


def register_all_placeholder_tools(mcp):
    """在 FastMCP 实例上注册 3 个工具。"""

    @mcp.tool()
    async def search_hardware(category: str, keyword: str = "") -> str:
        """查询硬件件目录。

        Args:
            category: 品类 (cpu|mainboard|memory|gpu|hdd|ssd|psu|cooler|case)
            keyword: 型号关键词（可选）
        """
        return (
            "[P1 占位] search_hardware 尚未实现。\n"
            f"参数: category={category}, keyword={keyword}\n"
            "此工具将在 P2-P3 爬虫+价格模块就位后回填。"
        )

    @mcp.tool()
    async def build_pc(
        budget_min: int, budget_max: int, goal: str, exclude: str = ""
    ) -> str:
        """按预算与需求生成装机配置方案。

        Args:
            budget_min: 预算下限（元）
            budget_max: 预算上限（元）
            goal: 需求描述（如 "2K高画质玩生化危机9"）
            exclude: 排除项，逗号分隔（如 "monitor,peripherals"）
        """
        return (
            "[P1 占位] build_pc 尚未实现。\n"
            f"参数: budget={budget_min}-{budget_max}, goal={goal}, exclude={exclude}\n"
            "此工具将在 P5 搭配算法就位后回填。"
        )

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
