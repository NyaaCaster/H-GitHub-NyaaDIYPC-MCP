"""
MCP 工具占位定义 — P1 阶段仅注册工具骨架，业务逻辑在 P4-P5 实现。

三个只读工具（审计整改 1：写操作永不暴露为 MCP 工具）：
  search_hardware  — P2-P3 爬虫+价格模块就位后回填
  build_pc         — P5 搭配算法就位后回填
  validate_build   — P4 兼容引擎就位后回填
"""


def register_all_placeholder_tools(mcp):
    """在 FastMCP 实例上注册 3 个占位工具。"""

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
        """校验一套装机配置的兼容性与功耗。

        Args:
            items: 件列表，JSON 数组格式 [{"category":"cpu","pro_id":"xxx"}, ...]
        """
        return (
            "[P1 占位] validate_build 尚未实现。\n"
            f"参数: items={items}\n"
            "此工具将在 P4 兼容引擎就位后回填。"
        )
