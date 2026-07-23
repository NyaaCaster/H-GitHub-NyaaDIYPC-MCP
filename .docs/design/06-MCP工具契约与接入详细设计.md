# 06 MCP 工具契约与 NyaaCLI 接入详细设计

> 对应 SSOT 第 3 节 + P6。工具面与调度链在此钉死，含审计整改 1（读写分离）。

---

## 1. 暴露给 claude 的 MCP 工具（只读，3 个）

审计整改 1：**只暴露只读查询工具，爬虫/写库不暴露**（防「NyaaCLI 鉴权边界已失效」被当作 RCE 跳板）。

### 1.1 `search_hardware`

```
search_hardware(category, price_min?, price_max?, keyword?, min_tier?, limit?=20)
→ [{pro_id, model, brand, category, price, tier_score, popularity, key_specs}]
```
- 按品类/价格区间/关键词/最低天梯分过滤，`effective_price` 计价，默认按性价比排序。
- 只读查库，不触发抓取。

### 1.2 `build_pc`

```
build_pc(budget_min, budget_max, goal, exclude?)
→ 见 05 §4 返回契约
```
- 全流程服务端计算，返回 2~3 方案。

### 1.3 `validate_build`

```
validate_build(items[])   # pro_id 列表
→ 见 04 §4 返回契约
```
- 校验任意件组合的兼容/功耗/总价，只读。

> 工具描述（docstring）写清：适用场景、参数语义、**「数字以本工具返回为准，勿自行估算」**，供 claude 正确调度。

---

## 2. 不暴露的运维面（仅容器内）

- 爬虫 `python -m app.crawler.run`（cron/手动）。
- 建表/迁移 `python -m app.db.migrate`。
- 天梯刷新、demand_map 导入。

这些**不进 MCP 工具清单**，claude 无法调用。

---

## 3. MCP 服务形态

- 独立容器（D1），MCP over stdio 或 HTTP（与现有 MCP 项目一致，参考 NyaaQiny-MCP 骨架）。
- NyaaCLI 通过 `.claude/mcp.json` 挂载本 MCP。
- 服务只依赖本地 SQLite + 出站调用 best-price MCP（补价）。

---

## 4. 调度链（端到端，实测代码确认）

```
QQ 用户自然语言
  → 猫猫 astrbot LLM 读 nyaa_think docstring 判定需装机
  → nyaa_think(task="配台8000-10000玩生化危机9 2K高画质，不要外设")
  → HTTP POST http://nyaacli:5113/task  {sender_id,nickname,task,session_origin}  (Bearer)
  → NyaaCLI headless claude (deepseek-v4-pro, acceptEdits, cwd=/root/DockerContainer)
  → claude 解析需求 → 调 DIY MCP：build_pc / search_hardware / validate_build
  → MCP 服务端 Python 计算 → 返回方案 JSON
  → claude 组织中文回复
  → NyaaCLI 回调 webhook 回灌 → 猫猫 push 回 QQ
```

- claude 负责：需求语义解析、工具编排、文字复核、中文措辞。
- MCP 负责：数据 + 全部数值计算（D3 铁律）。

---

## 5. NyaaCLI 接入配置（P6）

- 在 NyaaCLI 容器 `.claude/mcp.json` 增 DIY MCP server 条目（stdio：容器内命令；或 HTTP：容器网络地址）。
- 两容器同 macmini docker 网络，用服务名互联，不硬编 IP。
- claude 系统提示补一句「装机类请求调用 diypc 工具，数值以工具为准」。

---

## 6. 模块产物（P6 交付物）

```
app/mcp/
  __init__.py
  server.py       # MCP server + 工具注册
  tools.py        # search_hardware / build_pc / validate_build 封装
```

验证（P6 DoD）：NyaaCLI 内 claude 能列出并成功调用 3 工具；端到端从模拟 task 到方案 JSON 跑通；写操作确认不在工具清单。
