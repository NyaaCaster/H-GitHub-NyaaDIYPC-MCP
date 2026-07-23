---
name: commit-push
description: Create a git commit and optionally push to origin/master for the NyaaDIYPC-MCP project. Trigger when the user explicitly asks to commit, push, "提交", "推送", or "上传到 GitHub". Follows Conventional Commits style, never auto-commits without an explicit request, and refuses destructive operations.
---

# commit-push

为 NyaaDIYPC-MCP 项目执行 `git commit` 以及可选的 `git push origin master`。远端仓库：`https://github.com/NyaaCaster/H-GitHub-NyaaDIYPC-MCP.git`（**私有仓库**）。

## 触发条件

**只在用户明确要求时调用**，例如：
- "帮我提交"、"commit 一下"、"提交这些改动"
- "推送到 GitHub"、"push 到远端"、"上传"
- 显式调用 `/commit-push`

**严禁**在用户没有明确要求的情况下自动 commit 或 push——除非当前处于 Vibo Coding P 阶段收尾流程中。

## 提交信息风格

仓库采用 **Conventional Commits**（英文）：

| 类型     | 含义                                |
| -------- | ----------------------------------- |
| `feat:`  | 新功能或现有功能的增强              |
| `fix:`   | bug 修复                            |
| `chore:` | 构建、配置、辅助脚本等非业务改动    |
| `docs:`  | 仅文档变动                          |
| `refactor:` | 不改变行为的重构                 |
| `init:`  | 仅初始化提交时使用                  |

写作规则：
- 主语全部使用**英文**。
- `type:` 后跟空格和小写起首的简短描述。
- 主语短小（≤ 72 字符）；如需详述，在空行后写正文。
- **不附加 `Co-Authored-By` 行**。

## 标准流程

### 1. 提交前侦查

```
git status
git diff
git diff --cached
git log --pretty=format:"%h %s" -n 5
```

### 2. 暂存

- **始终按文件名显式 `git add <file>`**，禁止 `git add -A` / `git add .` / `git add -u`。

### 3. 起草提交信息

- 看 `git log` 确保风格一致。
- 多行信息用 HEREDOC 传入：
```bash
git commit -m "$(cat <<'EOF'
feat: short subject line

Optional body explaining the why.
EOF
)"
```

### 4. 推送（仅在用户要求时或 P 阶段收尾时）

- 默认目标：`origin master`。
- 若 `git push` 报 DPAPI/Session 0 错误，用 `windows-user-session-runner` skill 执行。
- 推送后 `git status` 验证。

## 绝不提交

- `.env`、`.env.*`（已被 `.gitignore` 排除）
- `.claude/settings.local.json`
- `.ref/`、`data/`、`*.db`、`*.log`
- 任何含 token / API key 的文件
- 大体积二进制（> 5 MB）

## 绝不做的操作

未经用户**显式书面同意**：
- ❌ `git push --force` / `--force-with-lease`
- ❌ `git commit --amend`（已推送的提交）
- ❌ `git reset --hard` / `git rebase`
- ❌ `--no-verify`、`--no-gpg-sign`
- ❌ 修改 `git config`
