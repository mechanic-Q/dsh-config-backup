#!/usr/bin/env bash
# tencent_refresh_cookie — 视频号 cookie 一键刷新（2026-09-15 定稿链路）
#
# 前置：Windows Chrome 已带 --remote-debugging-port=9222 启动且已登录视频号后台
#   （未启动时用 --launch-chrome 让本脚本代拉）
# 动作：CDP 导出 cookie+localStorage → 组装 Playwright storage_state →
#       写 cookies/tencent_diyi.json → 移走持久 profile 影子（兼容旧目录）→ sau tencent check
# 用法：
#   bash tencent_refresh_cookie.sh diyi            # 9222 已在线
#   bash tencent_refresh_cookie.sh diyi --launch   # 顺便拉起 Chrome 打开视频号后台
set -euo pipefail

ACCOUNT="${1:-diyi}"
LAUNCH_CHROME="${2:-}"

SAU_DIR="/home/lmr/social-auto-upload"
COOKIE_JSON="$SAU_DIR/cookies/tencent_${ACCOUNT}.json"
UA_FILE="$SAU_DIR/cookies/tencent_${ACCOUNT}.ua"
# 本脚本所在 skill 目录（#152：不再假设安装位置，同仓邻居从自身位置派生）
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PS1_SCRIPT="$SKILL_DIR/scripts/export_tencent_cookie.ps1"
CHROME_PROFILE="$SKILL_DIR/assets/chrome-tencent-profile"
CURL_WIN="/mnt/c/Windows/System32/curl.exe"
PS_EXE="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

if [ "$LAUNCH_CHROME" = "--launch" ]; then
  if "$CURL_WIN" -s -m 3 http://127.0.0.1:9222/json/version >/dev/null 2>&1; then
    echo "[refresh] 9222 已在线，跳过拉起"
  else
    echo "[refresh] 拉起 Windows Chrome（调试端口 9222）并打开视频号后台…"
    # Start-Process 立即返回；cmd.exe start 在 WSL interop 下偶发挂死（0915 实证）
    # 必须带 --user-data-dir：Chrome M136+ 禁止默认 profile 配 remote-debugging（0922 实证）
    # profile 目录同样从自身位置派生（#152）；用 wslpath 换成 Windows 口径
    CHROME_PROFILE_WIN="$(wslpath -w "$CHROME_PROFILE")"
    "$PS_EXE" -NoProfile -Command "Start-Process 'C:\Program Files\Google\Chrome\Application\chrome.exe' -ArgumentList '--remote-debugging-port=9222','--user-data-dir=${CHROME_PROFILE_WIN}','--no-first-run','https://channels.weixin.qq.com/platform/post/video'" >/dev/null 2>&1 || true
    sleep 6
  fi
fi

if ! "$CURL_WIN" -s -m 3 http://127.0.0.1:9222/json/version >/dev/null 2>&1; then
  echo "❌ 9222 不在线。先在 Windows 启动：chrome.exe --remote-debugging-port=9222 并登录视频号后台" >&2
  exit 1
fi

echo "[refresh] 1/4 CDP 导出 cookie…"
"$PS_EXE" -ExecutionPolicy Bypass -File "$(wslpath -w "$PS1_SCRIPT")" | tail -3

echo "[refresh] 2/4 组装 storage_state 并写入 $COOKIE_JSON"
python3 - "$ACCOUNT" <<'PYEOF'
import json, sys
from pathlib import Path

account = sys.argv[1]
base = Path("/home/lmr/social-auto-upload/cookies")
exp = json.load(open(f"/mnt/c/temp/tencent_{account}_export.json"))
ok_ss = {"Strict", "Lax", "None"}
cookies = []
for c in exp["cookies"]:
    c["sameSite"] = c.get("sameSite") if c.get("sameSite") in ok_ss else "Lax"
    cookies.append(c)
ls_raw = json.loads(exp.get("localStorage_raw") or "[]")
state = {
    "cookies": cookies,
    "origins": [{"origin": "https://channels.weixin.qq.com",
                 "localStorage": [{"name": k, "value": v} for k, v in ls_raw]}],
}
# 覆盖前备份当前文件（保留一次历史便于回滚）
target = base / f"tencent_{account}.json"
if target.exists():
    (base / f"tencent_{account}.json.bak").write_text(target.read_text(encoding="utf-8"))
target.write_text(json.dumps(state, ensure_ascii=False, indent=2))
print(f"  写入 {len(cookies)} cookies + {len(ls_raw)} localStorage")
PYEOF

echo "[refresh] 3/4 清理持久 profile 影子（防遮蔽 json 单一事实源）"
PROFILE_DIR="$SAU_DIR/cookies/tencent_profiles/${ACCOUNT}"
if [ -d "$PROFILE_DIR" ]; then
  mv "$PROFILE_DIR" "${PROFILE_DIR}.bak_$(date +%Y%m%d_%H%M%S)"
  echo "  已移走 $PROFILE_DIR"
else
  echo "  无残留，跳过"
fi

echo "[refresh] 4/4 sau 校验 cookie"
if ! [ -s "$UA_FILE" ]; then
  echo "  ⚠️ 缺 UA 侧车 $UA_FILE（2026-09-12 会话绑 UA 机制），从 9222 抓取…"
  UA=$("$CURL_WIN" -s -m 3 http://127.0.0.1:9222/json/version | python3 -c "import json,sys; print(json.load(sys.stdin)['User-Agent'])")
  printf '%s' "$UA" > "$UA_FILE"
  echo "  已写入 UA: $UA"
fi

CHECK_OUT="$(xvfb-run -a sau tencent check --account "$ACCOUNT" 2>&1)"
echo "$CHECK_OUT" | tail -3
if echo "$CHECK_OUT" | grep -qE "cookie 有效|(^|[^a-z])valid"; then
  echo "✅ 视频号 cookie 刷新完成且校验通过"
else
  echo "❌ check 未通过——确认 Chrome 里视频号后台处于登录态后重跑" >&2
  exit 1
fi
