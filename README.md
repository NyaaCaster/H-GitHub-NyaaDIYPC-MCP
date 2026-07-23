# 猫猫装电脑（NyaaDIYPC-MCP）

为 astrbot 机器人**猫猫**提供装机配置能力的 MCP（Model Context Protocol）服务。

QQ 用户用自然语言提出装机需求 → 猫猫经 `nyaa_think` → NyaaCLI 容器内 Claude 调度本 MCP 工具 → 返回**品牌型号 + 服务端算准的总价**的 1~3 套合理配置。

## 架构

```
QQ 用户自然语言
  → 猫猫(astrbot LLM) 判定需深度求解
  → nyaa_think(task="配台8000-10000玩生化危机9 2K高画质")
  → HTTP POST nyaacli:5113/task
  → NyaaCLI 容器内 headless claude
  → 调 diy-pc-mcp 工具: build_pc / search_hardware / validate_build
  → 服务端 Python 计算 → 方案 JSON
  → claude 组织中文回复 → webhook 回灌 → 猫猫推回 QQ
```

**D3 计算铁律**：所有数值计算（总价、功耗、预算判定、性价比排序）只在服务端 Python 完成，claude/LLM 直接引用结果，禁止自行心算或改写数字。

## 部署拓扑

```
macmini (U-MacMini-1, 192.168.31.141, Ubuntu x86_64)
├── nyaadiypc-mcp    :5115   ← 本项目
│   ├── Python 3.12 + FastMCP (Streamable HTTP)
│   ├── SQLite: 9 品类 ZOL 硬件库 + 需求映射表
│   ├── 爬虫: 每日 06:00 cron 全量刷新
│   └── MCP_API_KEY Bearer 鉴权
├── nyaacli           :5113   Claude Code 独立服务
│   └── .claude/mcp.json → http://192.168.31.141:5115/mcp
└── astrbot           :6185   猫猫机器人
```

## MCP 工具

| 工具 | 签名 | 说明 |
|------|------|------|
| `build_pc` | `(budget_min, budget_max, goal, exclude?)` | 需求映射 → 预算分配 → 逐类选件 → 兼容修复 → 预算收敛 → 多方案输出 |
| `search_hardware` | `(category, keyword?, min_price?, max_price?, min_tier?, limit?)` | 按品类/价格/关键词/天梯分查询硬件件 |
| `validate_build` | `(items[])` | 兼容性硬校验（C1-C9）+ 软约束（W1-W5）+ 功耗估算 + 总价核算 |

三个工具全部只读，爬虫/建表等写操作不暴露给 claude。

### build_pc 返回示例

```json
{
  "plans": [
    {
      "label": "推荐",
      "items": [
        {"category": "cpu",       "model": "…", "price": 1499},
        {"category": "gpu",       "model": "…", "price": 4599},
        {"category": "mainboard", "model": "…", "price": 749},
        {"category": "memory",    "model": "…", "price": 799},
        {"category": "ssd",       "model": "…", "price": 549},
        {"category": "psu",       "model": "…", "price": 499},
        {"category": "cooler",    "model": "…", "price": 159},
        {"category": "case",      "model": "…", "price": 399}
      ],
      "total": 7971,
      "in_budget": true,
      "compat_ok": true,
      "perf_note": "生化危机9 2k high (推荐)"
    }
  ],
  "demand_hit": {"source": "map", "game": "生化危机9", "resolution": "2k", "quality": "high"},
  "priced_at": "2026-07-23T…"
}
```

> ⚠️ 4K 原生渲染游戏配置会自动回绝（引导 2K）。实测 9 品类 25,380 件 ZOL 真机数据，8 件套方案兼容全过。

## 项目结构

```
NyaaDIYPC-MCP/
├── app/
│   ├── build/          # 搭配算法（D5-D6）
│   │   ├── demand.py       # 需求映射查表（game+resolution+quality → tier threshold）
│   │   ├── allocate.py     # 预算基线分配（CPU/GPU/主板/内存… 比例）
│   │   ├── select.py       # 逐类选件（依赖顺序：gpu→cpu→mainboard→memory→…）
│   │   ├── repair.py       # 兼容修复循环（调 validate 内核，最多 5 轮）
│   │   ├── converge.py     # 预算收敛（降档/升档，最多 6 轮）
│   │   └── build_pc.py     # 顶层编排 + 多方案输出
│   ├── compat/         # 兼容规则引擎（P4）
│   │   └── validate.py     # C1-C9 硬规则 + W1-W5 软规则 + 功耗估算
│   ├── crawler/        # ZOL 爬虫（P2）
│   │   ├── fetch.py        # HTTP 会话（httpx + GBK + Referer）
│   │   ├── getgoods.py     # GetGoods 列表页 9 品类全量分页
│   │   ├── parampage.py    # 深层 param 页补全（compat 关键字段）
│   │   ├── normalize.py    # 中文规格 → 归一化 compat JSON
│   │   ├── tianti.py       # CPU/GPU 天梯图解析
│   │   ├── store.py        # 幂等落库 + DB 备份 + 天梯分回填
│   │   └── run.py          # 编排入口 + CLI
│   ├── pricing/        # 价格模块（P3）
│   ├── mcp/            # MCP 工具注册 + Bearer 鉴权
│   └── db/             # SQLite 建表 + 初始化
├── server.py           # ASGI 入口（/health + /mcp）
├── Dockerfile          # Python 3.12-slim 多阶段构建
├── rebuild.py          # Windows 构建推送（NyaaDockerHUB）
├── restart.py          # macmini 拉取重启
├── docker-compose.yml       # 本地开发（build: .）
├── docker-compose.publish.yml  # 发布（引用私有仓镜像）
├── tests/              # pytest 152 用例
└── .docs/              # SSOT 开发计划 + 7 份详细设计 + 阶段交接
```

## 数据库

SQLite，表结构见 `.docs/design/02-数据模型详细设计.md`。

| 表 | 说明 |
|------|------|
| `hardware` | 9 品类硬件件（pro_id 主键，specs_json + compat JSON + 多层价格） |
| `compat` | 归一化兼容键（插槽/内存代际/板型/功耗/尺寸） |
| `perf_tier` | CPU/GPU 天梯分（ZOL 原始分 → 动态归一化 0-300） |
| `demand_map` | 需求映射表（game + resolution + quality → min/rec tier threshold） |
| `meta` | 爬虫状态、schema 版本 |

### 数据来源

- **硬件列表 + 规格**：ZOL 产品库，`GetGoods` API 列表页 + 深层 param 页
- **价格**：ZOL 京东价（主）+ best-price 淘宝价（次）
- **天梯分**：ZOL CPU/GPU 天梯图页面解析，动态归一化到 0-300
- **需求映射**：手工维护 `demand_map`（内置 6 款游戏 + 4 条通用回退档位）

当前库存：**9 品类 25,380 件**（cpu:220 / gpu:2,557 / mainboard:2,311 / memory:4,050 / hdd:675 / ssd:4,995 / psu:2,876 / cooler:4,995 / case:2,701），每日 06:00 cron 全量刷新。

## 搭配算法流程

```
build_pc(budget_min, budget_max, goal)
  │
  ├─ 1. 需求解析 → demand_map 查表 → 锁定 min_gpu_tier / min_cpu_tier / min_vram / min_ram
  │     未收录游戏 → __generic__ 回退 → 硬编码兜底
  │     4K + 游戏场景 → 自动回绝（引导 2K）
  │
  ├─ 2. 预算分配 → 按 gaming/balanced profile 分配各品类占比
  │
  ├─ 3. 逐类选件 → 依赖顺序: gpu→cpu→mainboard→memory→cooler→psu→case→ssd
  │     每类在预算占比内选"兼容 & tier 达标 & 有效价格最优"的件
  │
  ├─ 4. 兼容修复 → 调 validate 内核，不兼容则回溯换件（最多 5 轮）
  │
  ├─ 5. 预算收敛 → 总价超出/低于区间则降档/升档重选（最多 6 轮）
  │
  └─ 6. 多方案输出 → 2-3 套（够用/推荐/拉满），每套含件清单 + 总价 + 兼容状态
```

## 兼容性规则

| 规则 | 校验 |
|------|------|
| C1 CPU ↔ 主板 | `cpu.socket == mainboard.socket` |
| C2 主板 ↔ 内存 | `mainboard.mem_type == memory.mem_type` |
| C3 主板 ↔ 机箱 | 机箱支持主板 form_factor（ATX/M-ATX/ITX） |
| C4 散热器 ↔ CPU | `cpu.socket in cooler.cpu_socket_support` |
| C5 电源功率 | `psu.rated_w >= (cpu.tdp + gpu.tdp) × 1.5 + 80W` |
| C6 显卡 ↔ 机箱 | `gpu.length_mm <= case.max_gpu_len_mm - 10mm` |
| C7 散热器 ↔ 机箱 | `cooler.height_mm <= case.max_cooler_h_mm - 10mm` |
| C8 显卡 ↔ 电源 | 电源接口匹配 |
| C9 主板 ↔ SSD | M.2 插槽数量 |

另有 W1-W5 软约束（内存频率建议、芯片组代际、散热余量等），产生 warn 级别提示不阻塞方案产出。

## 快速开始

### 本地开发

```bash
# 1. 复制环境变量
cp .env.example .env
# 编辑 .env，填入 PRIVATE_DOCKER_REGISTRY_HOST 等

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务（首次启动自动建表 + 播种 demand_map）
python server.py
# → http://localhost:5115/health

# 4. 运行测试
python -m pytest tests/ -v
```

### Docker 构建推送

```bash
# 构建 + 推送到 NyaaDockerHUB
python rebuild.py

# 仅本地构建
python rebuild.py --skip-push
```

### macmini 部署

```bash
# 传输文件
scp -P 22141 docker-compose.publish.yml U-MacMini-1:/root/DockerContainer/NyaaDIYPC-MCP/docker-compose.yml
scp -P 22141 restart.py .env U-MacMini-1:/root/DockerContainer/NyaaDIYPC-MCP/

# 部署
ssh -p 22141 U-MacMini-1
cd /root/DockerContainer/NyaaDIYPC-MCP
python3 restart.py
```

### 手动爬虫

```bash
# macmini 容器内
docker exec nyaadiypc-mcp python -m app.crawler.run --category all

# 单品类
docker exec nyaadiypc-mcp python -m app.crawler.run --category cpu
```

## 环境变量

| 变量 | 用途 | 默认 |
|------|------|------|
| `MCP_PORT` | 服务端口 | `5115` |
| `MCP_HOST` | 监听地址 | `0.0.0.0` |
| `MCP_API_KEY` | Bearer 鉴权 token（不设为空则无鉴权） | — |
| `DIYPC_DB_PATH` | SQLite 路径 | `/app/data/diypc.db` |
| `PRIVATE_DOCKER_REGISTRY_HOST` | 私有仓库地址（禁硬编码） | — |
| `CRAWL_CRON` | 定时爬虫周期 | `0 6 * * *` |
| `DEEP_TOP_N` | 深参页抓取上限 | `300` |
| `PLAN_COUNT` | build_pc 方案数 | `3` |
| `REPAIR_MAX_ITER` | 兼容修复最大轮次 | `5` |
| `CONVERGE_MAX_ITER` | 预算收敛最大轮次 | `6` |

## 技术栈

| 层 | 技术 |
|------|------|
| 运行时 | Python 3.12+ |
| MCP 框架 | FastMCP (mcp >= 1.12) |
| HTTP 传输 | Starlette + uvicorn |
| 数据库 | SQLite（bind mount 持久化） |
| HTTP 客户端 | httpx（爬虫） |
| HTML 解析 | BeautifulSoup4 |
| 容器化 | Docker（python:3.12-slim，多阶段构建） |
| 测试 | pytest（152 用例） |

## 安全

- MCP 端 Bearer token 鉴权（对标 NyaaQiny-MCP 多 token 模式）
- 3 个 MCP 工具全部只读，写操作（爬虫/建表）不入工具面
- 私有仓库地址仅经 `.env` 注入，Git 跟踪文件掩码为 `<PRIVATE_REGISTRY>`
- 爬虫合规：控制频率（≥1s 间隔）、带 Referer、仅抓公开规格用于个人装机参考
- DB 写前备份，SQLite 文件不入 Git

## 相关项目

| 项目 | 关系 |
|------|------|
| [NyaaCLI](https://github.com/NyaaCaster/NyaaCLI) | Claude Code 调度容器，通过 mcp.json 调用本服务 |
| [astrbot_plugin_nyaa_IntelligenceFrame](https://github.com/NyaaCaster/astrbot_plugin_nyaa_IntelligenceFrame) | 猫猫智能框架，nyaa_think 工具调度 |
| [NyaaQiny-MCP](https://github.com/NyaaCaster/H-GitHub-NyaaQiny-MCP) | MCP 项目工程骨架参考 |
| [NyaaChat](https://github.com/NyaaCaster/H-GitHub-NyaaChat) | 主要长期项目，同 macmini 部署 |
