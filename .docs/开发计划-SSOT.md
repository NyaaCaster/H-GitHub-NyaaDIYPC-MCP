# NyaaDIYPC-MCP 开发计划 SSOT（单一事实来源）

> 本文件是本项目开发阶段的**唯一事实来源**。架构方案、技术选型、关键约束、环境变量、阶段划分均以此为准。
> 配套文档：`.docs/架构审计报告.md`（安全与设计审计）、`.docs/design/`（各模块详细设计，执行期权威）、`.ref/`（探索期参考资料，不入 Git）。
>
> 定稿日期：2026-07-23 ｜ 状态：详细设计完成，执行期零决策，待用户批准后进入 P1

---

## 详细设计索引（执行期权威）

为确保开发执行期不再需要临时决策/临时设计，各模块已产出详细设计文档，落 `.docs/design/`。**执行期以详细设计为准**；本 SSOT 第 2~5 节为概览，若与详细设计有细化差异（如数据模型采用结构化列而非纯 JSON），以详细设计为权威。

| 文档 | 覆盖 | 对应阶段 |
|------|------|---------|
| [01-爬虫模块详细设计](design/01-爬虫模块详细设计.md) | D2 数据来源、GetGoods/深参/天梯解析、抓取工程、幂等落库、触发方式 | P2 |
| [02-数据模型详细设计](design/02-数据模型详细设计.md) | SQLite 全表 DDL（hardware/compat/perf_tier/demand_map/meta）、完整性约束 | P1/贯穿 |
| [03-价格与对齐详细设计](design/03-价格与对齐详细设计.md) | D4 价格优先级、effective_price、best 补价强制过滤层、型号归一匹配算法 | P3 |
| [04-兼容引擎与D3计算详细设计](design/04-兼容引擎与D3计算详细设计.md) | D3 计算铁律、C1-C9 硬规则+W1-W5 软规则、功耗估算、validate_build | P4 |
| [05-需求映射与D6搭配算法详细设计](design/05-需求映射与D6搭配算法详细设计.md) | D5 需求映射+回退档位、D6 预算基线分配/选件/兼容修复/预算收敛/多方案 | P5 |
| [06-MCP工具契约与接入详细设计](design/06-MCP工具契约与接入详细设计.md) | 3 只读工具契约、读写分离、调度链、NyaaCLI 接入 | P6 |
| [07-部署与环境详细设计](design/07-部署与环境详细设计.md) | 仓库目录、.env 全集、rebuild/restart、macmini 部署、安全红线 | P1/P7 |

## 0. 项目目标与范围边界

### 0.1 一句话目标

为 astrbot 机器人**猫猫**提供一个装机配置工具：QQ 用户用自然语言提出装机需求（预算区间 + 游戏/分辨率/画质目标 + 排除项），猫猫经 `nyaa_think` → NyaaCLI 容器内 claude 调度本 MCP 服务，返回**品牌型号价格 + 服务端算准的总价**的 1~3 套合理配置。

### 0.2 目标用户旅程（North Star）

```
QQ 用户 →「为我配置一台能在 2K 分辨率下尽量流畅玩生化危机9 中高画质的电脑，
          不需要显示器等外设，预算 8000~10000」
  → 猫猫(astrbot LLM) 判定需深度求解 → 调 nyaa_think
  → NyaaCLI 容器内 claude → 调 diy-pc-mcp 工具 build_pc(...)
  → 服务端: 需求映射→档次锁定→预算分配→逐类选件→兼容校验→总价核算(Python)
  → 返回 1~3 套方案(每套含各件品牌/型号/价格 + 总价 + 是否落在预算区间)
  → claude 复核微调(可换同价位更合适的牌子) → webhook 回灌 → 主动推回 QQ
```

### 0.3 范围内（V1）

- 9 大品类硬件库（CPU/主板/内存/显卡/机械硬盘/固态硬盘/电源/散热器/机箱）
- 预算区间驱动的整机搭配算法（硬编基线）
- 兼容性硬校验（插槽/内存代际/功率/尺寸）
- 服务端 Python 精确价格核算（LLM 零参与数字加法）
- MCP 工具暴露，供 NyaaCLI 容器内 claude 调用

### 0.4 范围外（V1 不做）

- 显示器/键鼠/外设选配（用户明确"不需要外设"）
- 笔记本/整机成品推荐
- 超频/水冷定制/RGB 灯效偏好
- 二手/垃圾佬配置
- 前端管理界面（V1 纯 MCP 后端，如需运维再议）

---

## 1. 已确认的架构方案

### 1.1 部署拓扑（macmini 生产环境）

```
macmini (U-MacMini-1, 192.168.31.141, Ubuntu x86_64, Docker via Snap)
├── astrbot            :6185(WebUI) / :6199(internal)
│    └── 猫猫智能框架插件 → nyaa_think llm_tool
├── nyaacli            :5113   容器内独立 claude code (Bash全开 + MCP + WebFetch)
│    └── .claude/mcp.json 网络挂载 → diy-pc-mcp
├── nyaalibrary-mcp    :5114   (既有，参考骨架)
└── diy-pc-mcp         :51XX   【本项目新增】
     ├── SQLite: 硬件库 + 兜底价 + 需求映射表
     ├── 爬虫模块(定时任务): ZOL GetGoods + param 深层页
     ├── 价格模块: ZOL京东价(主) + best-price补淘宝价(次)
     ├── 兼容规则引擎(Python)
     ├── 搭配算法(Python 硬编基线)
     └── MCP 工具: search_hardware / build_pc / validate_build
私有仓库 NyaaDockerHUB: 192.168.31.142:5000 (LAN 拉取) / localhost:5000 (本地推送)
```

### 1.2 调度链路（一手读代码确认）

```
QQ → 猫猫LLM(deepseek-v4-pro) 读 nyaa_think docstring 自主决策
   → HTTP POST 192.168.31.141:5113/task {sender_id, nickname, task, session_origin}
   → NyaaCLI 容器 headless claude:
        claude -p "[当前请求来自 sender_id=..,昵称=..]\n{原话}"
               --model deepseek-v4-pro --permission-mode acceptEdits --output-format json
        cwd=/workspace
   → 容器内 claude 调 diy-pc-mcp 工具(经 .claude/mcp.json)
   → webhook /callback 回灌 → 猫猫主动推回 QQ
```

### 1.3 六项关键决策（已与用户拍板 2026-07-23）

| # | 决策点 | 结论 |
|---|--------|------|
| D1 | 工具形态 | **独立 MCP 服务容器**（对标 nyaalibrary-mcp），非 skill/现爬 |
| D2 | 数据存储 | **SQLite + 定时爬虫任务**，查询命中本地库 |
| D3 | 价格计算 | **全部在服务端 Python 完成**，LLM 零参与数字加法 |
| D4 | 价格来源 | **ZOL 京东价为主 + best-price 实时补（主要补淘宝价）** |
| D5 | 游戏档次知识 | **天梯图 + 需求映射表**（服务端维护，可审计） |
| D6 | 搭配算法 | **硬编基线 + claude 复核微调** |

### 1.4 仓库与文档归属（已拍板）

- 代码仓库：`H:\GitHub\NyaaDIYPC-MCP`
- GitHub：`https://github.com/NyaaCaster/H-GitHub-NyaaDIYPC-MCP.git`（已建未初始化）
- 探索期资料 `.docs/DIY_PC` 已搬迁至本仓 `.ref/`，并由 `.gitignore` 排除跟踪
- 设计文档落 `.docs/`

---

## 2. 数据模型（SQLite）

> ⚠️ 本节为概览示意（JSON sketch）。**执行期建表以 [02-数据模型详细设计](design/02-数据模型详细设计.md) 的 DDL 为权威**——详细设计采用「结构化列 + specs_json 原文」双写（结构化列供 SQL 过滤与兼容校验），比本节纯 JSON 示意更利于查询与索引。

### 2.1 硬件件表 `hardware`

单件通用字段 + 品类专属规格（specs JSON）+ 归一兼容键（compat JSON）+ 兜底价。

```jsonc
{
  "category": "cpu",             // cpu/mainboard/memory/gpu/hdd/ssd/psu/cooler/case
  "subcate_id": 28,              // ZOL 品类 ID（爬虫用）
  "pro_id": "xxxx",              // ZOL 产品 ID（唯一）
  "manu_id": "xxx",              // 厂商 ID
  "name": "Intel 酷睿 i5-14600KF",
  "specs": { /* 原始中文键值，来自深层 param 页 */ },
  "compat": {                    // 归一化兼容键（规则引擎用）
    "socket": "LGA1700",         // CPU/主板
    "mem_type": "DDR5",          // 主板/内存
    "form_factor": "ATX",        // 主板/机箱
    "tdp_w": 125,                // CPU/GPU 功耗（电源核算）
    "length_mm": 336,            // 显卡长度/机箱限长
    "cpu_socket_support": ["LGA1700","LGA1200"]  // 散热器支持插槽
  },
  "price": {
    "zol_jd": 1899,              // ZOL 抓的京东价（主基准）
    "taobao": 1850,              // best-price 补的淘宝价（次）
    "zol_min": 1799,             // ZOL 多商家最低价（兜底）
    "source": "zol_jd",          // 实际采用来源
    "priced_at": "2026-07-23T..."
  },
  "popularity": 4.5,             // ZOL 人气/关注度（同价位排序辅助）
  "tier": null,                  // CPU/GPU 天梯档次分（映射表关联）
  "param_url": "...",
  "detail_url": "...",
  "fetched_at": "2026-07-23T..."
}
```

对应 SQLite 表：主键 `pro_id`，`specs`/`compat`/`price` 存 JSON 文本列，`category`/`subcate_id`/`tier` 建索引。

### 2.2 天梯档次表 `perf_tier`

```jsonc
{ "kind": "gpu", "name": "RTX 4070", "tier_score": 178, "vram_gb": 12, "pro_id": "xxxx" }
{ "kind": "cpu", "name": "i5-14600KF", "tier_score": 145, "pro_id": "xxxx" }
```

来源：ZOL 天梯图 `/soc/` 页（P2 探测）。`tier_score` 为归一化性能分，供需求映射表按档次锁定。

### 2.3 需求映射表 `demand_map`（D5 核心）

把"游戏 + 分辨率 + 画质目标"映射到"最低 GPU/CPU 档次 + 显存/内存下限"。人工维护 + 可审计：

```jsonc
{
  "game": "生化危机9",           // 别名归一：re9 / biohazard9
  "resolution": "2K",           // 1080P / 2K / 4K
  "quality": "中高",            // 低 / 中 / 中高 / 高 / 极致
  "target": "流畅",             // 流畅(≈60fps) / 高帧(≈120fps)
  "min_gpu_tier": 165,          // 对应 perf_tier.tier_score 下限
  "min_cpu_tier": 130,
  "min_vram_gb": 12,
  "min_ram_gb": 32,
  "note": "参考同类 3A 大作 2K 需求推定；新游戏无实测时按同引擎/同代对标"
}
```

> V1 先内置一批主流 3A 游戏 + 通用档位兜底（"未知游戏按 3A 大作同档处理"）。映射来源在 `note` 留痕，便于后续修正。

---

## 3. MCP 工具契约（对外接口）

> 本节为契约概览。**执行期工具签名/返回结构以 [06-MCP工具契约与接入详细设计](design/06-MCP工具契约与接入详细设计.md) 与 [05](design/05-需求映射与D6搭配算法详细设计.md) §4 为权威**（仅暴露 3 个只读工具，写操作不入工具面）。

三个工具，全部由服务端完成计算，claude 只读结果、只做语义复核。

### 3.1 `search_hardware`

```
search_hardware(category, filters?, price_min?, price_max?, limit?) -> list[件]
```

按品类 + 规格过滤 + 价格区间查本地库，返回件列表（含算好的当前采用价）。用于 claude 微调时换件。

### 3.2 `build_pc`（主工具）

```
build_pc(budget_min, budget_max, goal, exclude?) -> BuildResult
  goal   = {game?, resolution?, quality?, target?, usage?}  // 结构化或自然语言
  exclude= ["monitor","peripheral",...]                      // 排除项
```

服务端流程（硬编基线）：
```
1. 解析 goal → 查 demand_map → 锁定 min_gpu_tier/min_cpu_tier/min_vram/min_ram
2. 核心件锁定：在 tier 达标 & 价格合理的 GPU/CPU 里选性价比最优
3. 预算分配：按整机预算给各品类分配占比上限（GPU~35% / CPU~18% ...，可配置）
4. 逐类选件：每类在预算占比内选"兼容 & 性价比最优 & 人气高"的件
5. 兼容校验：调 validate_build 内核，不过则回溯换件
6. 总价核算(Python 精确加法) → 落在 [budget_min,budget_max] 则收，否则调档重试
7. 返回 1~3 套（可给"极致性价比/均衡/预算上限拉满"三档）
```

返回结构：
```jsonc
{
  "plans": [{
    "label": "均衡推荐",
    "items": [{"category":"cpu","name":"...","price":1899,"reason":"..."}, ...],
    "total": 9299,               // Python 算准
    "in_budget": true,
    "compat_ok": true,
    "perf_note": "预计 2K 中高画质生化9 稳定 60fps+"
  }],
  "demand_hit": { /* 命中的 demand_map 行，供 claude 说明依据 */ },
  "priced_at": "..."
}
```

### 3.3 `validate_build`

```
validate_build(items[]) -> {compat_ok, issues[], total, in_budget?}
```

对一套已有清单做兼容性硬校验 + 总价核算。claude 微调换件后回调此工具复核，确保改动不破坏兼容、总价重新算准。

### 3.4 计算铁律（D3）

**任何数字加总、预算比对、性价比排序，只允许在服务端 Python 完成。** MCP 工具返回的 `total`/`in_budget`/`compat_ok` 是权威值，claude/猫猫 一律直接引用，禁止自行心算或改写数字。这从根上消除 LLM 数字拟合错误。

---

## 4. 兼容性硬约束（规则引擎）

| 规则 | 校验 |
|------|------|
| CPU ↔ 主板 | `cpu.socket == mainboard.socket` |
| 主板 ↔ 内存 | `mainboard.mem_type == memory.mem_type`（DDR4/DDR5 不可混） |
| 主板 ↔ 机箱 | `case` 支持 `mainboard.form_factor`（ATX/M-ATX/ITX 尺寸包含关系） |
| 散热器 ↔ CPU | `cpu.socket in cooler.cpu_socket_support` |
| 电源功率 | `psu.rated_w >= (cpu.tdp + gpu.tdp) × 冗余系数(≈1.5) + 其他余量` |
| 显卡 ↔ 机箱 | `gpu.length_mm <= case.max_gpu_len_mm` |
| 散热器 ↔ 机箱 | `cooler.height_mm <= case.max_cooler_h_mm`（风冷限高） |

> 兼容关键字段来自**深层 param 页**（列表内嵌参数是截断版，散热器"适用范围"、SSD"外形尺寸"、机箱"USB 接口"等会被切断——见 `.ref/ZOL爬虫方案`）。故爬虫必须抓深层页填 compat。

---

## 5. 环境变量约定（`.env`，不入 Git）

| 变量 | 用途 | 示例 |
|------|------|------|
| `APP_PORT` | 服务监听端口 | `5115`（P1 已定，避开 5113/5114/5104） |
| `DB_PATH` | SQLite 路径（bind mount 到 macmini） | `/data/diypc.db` |
| `PRIVATE_DOCKER_REGISTRY_HOST` | 私有仓库地址（禁硬编码） | 注入，输出掩码 `<PRIVATE_REGISTRY>` |
| `CRAWL_CRON` | 定时爬虫周期 | `0 4 * * *`（每日 4:00 刷新） |
| `BEST_PRICE_ENABLED` | 是否启用 best-price 补淘宝价 | `true` |
| `MCP_AUTH_TOKEN` | MCP 调用鉴权（如需，见审计） | 注入 |

> macmini 大文件/DB 落 `E:\DockerRes` 规则对应 macmini 侧 `/root/DockerContainer/DockerRes/nyaadiypc/`。

---

## 6. 版本与阶段划分（V1）

| 阶段 | 内容 | 可验证交付 | 状态 |
|------|------|-----------|------|
| P1 | 工程骨架：仓库初始化 + MCP 服务脚手架 + SQLite schema + Dockerfile + rebuild/restart + compose（详见 [02](design/02-数据模型详细设计.md)、[07](design/07-部署与环境详细设计.md)） | 服务能起、/health 通、空库建表 | ✅ |
| P2 | 爬虫模块：GetGoods 全量 + param 深层页 + 天梯图，落 9 品类库（详见 [01](design/01-爬虫模块详细设计.md)） | 各品类抓够样本、compat 字段齐、天梯表就位 | ✅ |
| P3 | 价格模块：ZOL 京东价为主 + best-price 补淘宝价 + 过滤层（串货/整机剔除）（详见 [03](design/03-价格与对齐详细设计.md)） | 抽样件价格准确、来源标注正确 | ✅ |
| P4 | 兼容规则引擎 + `validate_build` 工具（详见 [04](design/04-兼容引擎与D3计算详细设计.md)） | 已知兼容/不兼容组合校验正确 | ✅ |
| P5 | 需求映射表 + `build_pc` 搭配算法（硬编基线）（详见 [05](design/05-需求映射与D6搭配算法详细设计.md)） | 示例需求出 1~3 套方案、总价算准、落预算区间 | ✅ |
| P6 | MCP 工具封装 + NyaaCLI `.claude/mcp.json` 接入 + 端到端联调（详见 [06](design/06-MCP工具契约与接入详细设计.md)） | 容器内 claude 能调通三工具、模拟 QQ 需求走通全链路 | ✅ |
| P7 | macmini 部署收尾（迁移规范）+ 定时爬虫任务 + 文档交接（详见 [07](design/07-部署与环境详细设计.md)） | 真机 E2E、定时刷新生效 | ✅ |

状态符号：⬜ 未开始 ｜ 🟡 进行中 ｜ ✅ 已完成

---

## 7. 关键约束（安全红线，MUST）

- **调 DB 写操作前必先备份 `.db` 文件**（含 DELETE/UPDATE/DROP/ALTER/VACUUM）。
- **测试后必清理**：删测试数据/临时脚本，`git status` 确认无残留。
- **自动化脚本用 Python**，不写 .ps1/.sh（rebuild.py/restart.py）。
- **私有仓库地址禁硬编码**，走 `.env` 注入，输出掩码 `<PRIVATE_REGISTRY>`。
- **macmini 迁移前必先停 Windows 源容器**（最高优先级步骤）。
- **禁止未经用户明确要求启动容器**（docker compose up/run/start）；rebuild.py 只构建推送不运行。
- **爬虫合规**：控制频率、带 Referer、尊重 robots，仅抓公开规格/价格用于个人装机参考，不做商业分发。
- **NyaaCLI 鉴权旁路（已知高危，见审计报告 §审计-3）**：本 MCP 若暴露写操作需自带鉴权，不依赖 NyaaCLI 的 token 边界。

---

## 8. 相关文件

| 文件 | 说明 |
|------|------|
| `.docs/开发计划-SSOT.md` | 本文件（唯一事实来源，概览+索引） |
| `.docs/架构审计报告.md` | 安全与设计审计 |
| `.docs/design/01-爬虫模块详细设计.md` | P2 爬虫详细设计（执行期权威） |
| `.docs/design/02-数据模型详细设计.md` | SQLite 全表 DDL（执行期权威） |
| `.docs/design/03-价格与对齐详细设计.md` | P3 价格来源/补价过滤/匹配算法（执行期权威） |
| `.docs/design/04-兼容引擎与D3计算详细设计.md` | P4 兼容规则+功耗+计算铁律（执行期权威） |
| `.docs/design/05-需求映射与D6搭配算法详细设计.md` | P5 需求映射+搭配算法（执行期权威） |
| `.docs/design/06-MCP工具契约与接入详细设计.md` | P6 工具面+调度链+接入（执行期权威） |
| `.docs/design/07-部署与环境详细设计.md` | P1/P7 部署+环境变量+安全红线（执行期权威） |
| `.ref/ZOL爬虫方案-硬件数据与装机合理性.md` | 爬虫两源方案（探索期，不入 Git） |
| `.ref/best-price-mcp-使用说明.md` | 价格源使用说明（探索期） |
| `.ref/query_price.py` | best-price 调用样例 |

