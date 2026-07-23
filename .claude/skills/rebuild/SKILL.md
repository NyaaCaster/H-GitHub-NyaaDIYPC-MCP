---
name: rebuild
description: Rebuild the NyaaDIYPC-MCP Docker image and push to private registry. Use this whenever the project needs a Docker rebuild + push (e.g., after Dockerfile, docker-compose.yml, or Python source changes). Runs rebuild.py — a cross-platform Python script that works on Windows, Linux, and macOS.
---

# rebuild

本项目需要重新编译 Docker 镜像并推送到 NyaaDockerHUB 私有仓库时调用此 skill。

## 触发场景

- 用户明确要求"重新编译"、"重建镜像"、"rebuild"。
- 改动了 `Dockerfile`、`docker-compose.yml`。
- 改动了进入镜像的源码：`app/**/*.py`、`server.py`、`requirements.txt`。
- 通过 `/rebuild` 显式调用。

不需要 rebuild 的情况：仅改了 `.env`——它由 `docker-compose.yml` 的 `env_file` 在容器启动时读取，没烘进镜像。

## 执行方式

所有平台统一使用 Python 脚本：

```
python rebuild.py
```

- `--no-cache`：强制无缓存完全重建。
- `--skip-push`：仅本地构建，不推送私有仓库（离线调试用）。

脚本流程：构建镜像（tag = git short SHA + latest）→ 推送到 NyaaDockerHUB 私有仓库 → 仓库端清理旧 tag → 本地清理旧 tag 与悬空镜像。

⚠️ **rebuild.py 只构建推送，不启动容器**（工作空间铁律）。macmini 侧部署由 `restart.py` 独立完成。

## 执行规则

- 执行前请确认工作目录是项目根目录（含 `docker-compose.yml`）。
- 执行后向用户简要汇报：脚本是否成功结束、推送的 tag。
- 输出中的仓库地址自动掩码为 `<PRIVATE_REGISTRY>`。

## 不要做的事

- 不要绕过脚本直接调用 `docker compose build`/`push`——使用脚本能保证流程一致。
- **不要在 rebuild 后自动启动容器**（需要用户明确许可）。
