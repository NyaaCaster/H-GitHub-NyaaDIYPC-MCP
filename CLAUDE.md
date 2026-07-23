# NyaaDIYPC-MCP

## 项目概况

为 astrbot 机器人**猫猫**提供装机配置 MCP（Model Context Protocol）服务。QQ 用户用自然语言提出装机需求（预算区间 + 游戏/分辨率/画质目标 + 排除项），猫猫经 `nyaa_think` → NyaaCLI 容器内 claude 调度本 MCP 工具，返回**品牌型号 + 服务端算准的总价**的 1~3 套合理配置。

运行形态：**Python 3.12+**，MCP SDK Streamable HTTP 传输，Bearer Token 鉴权（对标 NyaaQiny-MCP），Docker 部署（容器端口 **5115**）。

完整设计文档：`.docs/开发计划-SSOT.md` + `.docs/design/01~07`。

## 交流语言

默认始终以**简体中文**与用户交流，除非用户在某次对话中明确要求改用其他语言。

- 适用范围：所有面向用户的文字输出（解释、总结、提问、错误说明等）。
- 代码、标识符、命令行参数、文件路径、提交信息等仍按惯例使用英文。

## Docker 构建推送（rebuild.py）

当需要构建镜像并推送到 NyaaDockerHUB 私有仓库时，使用 `rebuild` skill：

- 统一执行 `python rebuild.py`（跨平台 Python 脚本）。
- `--no-cache`：强制完全重建；`--skip-push`：仅本地构建不推送。
- **rebuild.py 只构建推送，不启动容器**（工作空间铁律：禁止未经明确要求启动容器）。macmini 侧部署由 `restart.py` 独立完成。
- 仅改 `.env` 不需要 rebuild。
- 详细规则见 `.claude/skills/rebuild/SKILL.md`。

## Git 提交与推送

当用户明确要求"提交"、"commit"、"推送"、"push"、"上传到 GitHub"时，使用 `commit-push` skill：

- **未经用户明确请求，绝不自动 commit / push**（P 阶段收尾除外）。
- 提交信息使用 **Conventional Commits**（英文，小写起首）；**不**附加 `Co-Authored-By` 行。
- 始终 `git add <file>` 显式指定文件，**禁止** `git add -A` / `git add .`。
- `.env`（含凭证）、`.claude/settings.local.json`、`.ref/`、`data/` **绝不入库**。
- 远端仓库：`https://github.com/NyaaCaster/H-GitHub-NyaaDIYPC-MCP.git`（**私有仓库**），主分支 `master`。
- 详细规则见 `.claude/skills/commit-push/SKILL.md`。

## 数据库备份（MUST）

修改 DB 数据前（含 DELETE/UPDATE/DROP/ALTER/VACUUM 等写入），**必须先备份 `.db` 文件**——复制并带时间戳命名。操作确认成功后再删除备份。

## 测试后清理（MUST）

任何测试完成后，删除临时脚本/日志/dump/测试注册数据，`git status` 确认无残留。

## 设计文档（开发期权威）

所有模块的详细设计落 `.docs/design/`，**执行期以详细设计为权威**。SSOT（`.docs/开发计划-SSOT.md`）为概览和索引。新接触本项目时，先读 SSOT 了解全貌，再按需读对应设计文档。
