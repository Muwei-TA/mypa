#!/usr/bin/env bash
# 部署前检查（依据 PRD v1.1 §22.1、§17.6）。只读检查，不修改任何系统设置。
set -uo pipefail
fail=0
ok()   { echo "  [OK]   $*"; }
warn() { echo "  [WARN] $*"; }
bad()  { echo "  [FAIL] $*"; fail=1; }

echo "== 系统"
[ "$(uname -s)" = "Darwin" ] && ok "macOS" || bad "需要 macOS"
[ "$(id -un)" = "pa" ] && ok "当前用户为 pa" || warn "建议使用专用的普通用户 pa（§22.1）"
id -Gn | grep -qw admin && warn "当前用户有管理员权限，建议去掉（§22.1）" || ok "非管理员用户"

echo "== 电源（§22.1）"
pmset -g | grep -E '^\s*sleep\s+0' >/dev/null && ok "系统睡眠已关闭" || warn "建议关闭自动睡眠"
pmset -g | grep -E 'autorestart\s+1' >/dev/null && ok "断电后自动启动" || warn "建议开启断电后自动启动"
pmset -g | grep -E 'womp\s+1' >/dev/null && ok "网络唤醒" || warn "建议开启网络唤醒"

echo "== FileVault / 自动登录（§17.6，二选一）"
fdesetup status 2>/dev/null | grep -q "On" && warn "FileVault 已开启：重启后需要你远程解锁一次，解锁前 C 档引擎不可用" || warn "FileVault 未开启：只有选择开机自动登录（C 档引擎全天可用）时才应如此"

echo "== 工具"
for b in git sqlite3 uv rclone cloudflared; do command -v "$b" >/dev/null && ok "$b" || warn "$b 未安装（install.sh 会安装）"; done

echo "== 磁盘（§16.3：剩余空间应大于 10%）"
used=$(df -P "$HOME" | awk 'NR==2{gsub("%","",$5);print $5}')
[ "${used:-100}" -lt 90 ] && ok "磁盘已用 ${used}%" || bad "磁盘已用 ${used}%，剩余空间不足 10%"

exit $fail
