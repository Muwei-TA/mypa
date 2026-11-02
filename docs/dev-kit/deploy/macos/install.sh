#!/usr/bin/env bash
# PA 安装脚本（依据 PRD v1.1 §22.1–22.2）
# 用法：以 pa 用户身份运行  bash deploy/macos/install.sh [<pa-repo-url>]
set -euo pipefail

PA_REPO="${1:-<pa-repo>}"
PA_HOME="${HOME}/pa"

echo "==> 部署前检查"
bash "$(dirname "$0")/scripts/preflight.sh" || { echo "preflight 未通过"; exit 1; }

echo "==> 安装系统依赖（§22.1）"
command -v brew >/dev/null || { echo "需要先安装 Homebrew"; exit 1; }
brew install uv git rclone cloudflared sqlite age sops
# 如果用 1Password 作为密钥后端：brew install 1password-cli
command -v npm >/dev/null && npm i -g @openai/codex || echo "提示：未检测到 npm，Codex CLI 需要另行安装"
# 可选：Claude Code（engines.yaml 中 claude_code 默认 enabled: false）

echo "==> 获取 PA 代码（§22.2）"
if [ ! -d "${PA_HOME}" ]; then git clone "${PA_REPO}" "${PA_HOME}"; fi
cd "${PA_HOME}"
uv sync --frozen

echo "==> 初始化配置和数据目录"
uv run pa init          # 生成 ~/.config/pa 配置模板和 ~/pa-data 数据目录
uv run pa migrate       # 建表

echo "==> 配置目录纳入 git 管理（§18.2，不含任何明文密钥）"
if [ ! -d "${HOME}/.config/pa/.git" ]; then
  git -C "${HOME}/.config/pa" init -q
  printf '.env\n*.pem\n' > "${HOME}/.config/pa/.gitignore"
  git -C "${HOME}/.config/pa" add -A && git -C "${HOME}/.config/pa" commit -qm "pa init" || true
fi

cat <<'EOF'
==> 后续步骤（见 05-部署与运维指南.md）
  1. pa secret set ...            写入密钥（§22.3）
  2. 填好 ~/.config/pa/*.yaml 中的 <…> 占位符
  3. 配置 GitHub、Gitea、飞书、Cloudflare、WebDAV（§22.4）
  4. pa sandbox images build      构建容器镜像（v1.1·K5）
  5. pa config validate && pa service install && pa doctor
EOF
