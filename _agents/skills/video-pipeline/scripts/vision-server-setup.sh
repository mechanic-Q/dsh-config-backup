#!/usr/bin/env bash
# vision-server-setup.sh — T1(#102)：本地判读服务 llama-server 供给脚本（幂等可续传）。
# 下载 pin 版 llama.cpp CPU 构建到 bin/，三档 Qwen3-VL Q4_K_M + mmproj(F16) 到 models/。
# Windows 侧 CPU 常驻（ADR 0007：与烧卡窗口零显存交互），端口 8100。
# 用法：bash scripts/vision-server-setup.sh [--models-only|--bin-only]
# env：VJ_HOME（默认 E:\AI\LLM\vision-judge）、LLAMA_BUILD（默认 pin 版）、
#      HF_BASE（默认 huggingface.co，被墙可换 hf-mirror.com）
set -uo pipefail

LLAMA_BUILD="${LLAMA_BUILD:-b11603}"
# 缺省走 hf-mirror（10-14 实测：HF 直连 85MB 处停滞死锁，镜像 3MB/s 且支持断点续传）
HF_BASE="${HF_BASE:-https://hf-mirror.com}"
case "${VJ_HOME:-}" in
  "") if [ -d /mnt/e ]; then VJ_HOME=/mnt/e/AI/LLM/vision-judge; else VJ_HOME=/e/AI/LLM/vision-judge; fi ;;
esac
BIN_DIR="$VJ_HOME/bin"; MODEL_DIR="$VJ_HOME/models"
mkdir -p "$BIN_DIR" "$MODEL_DIR"

want_bin=1; want_models=1
[ "${1:-}" = "--models-only" ] && want_bin=0
[ "${1:-}" = "--bin-only" ] && want_models=0

done_ok() { # 本地大小 == 远端大小 → 视为完整（416=已下完的优雅出口）
  local remote local_sz
  remote=$(curl -sIL -m 15 "$1" | tr -d '\r' | awk 'tolower($1)=="content-length:" {sz=$2} END {print sz}')
  local_sz=$(stat -c %s "$2" 2>/dev/null || echo 0)
  [ -n "$remote" ] && [ "$local_sz" = "$remote" ]
}

# HF LFS 元数据：文件名 → "大小 sha256"（tree API 的 lfs.oid；10-14 教训：
# 新旧进程曾并行写同一文件，光对大小不够，必须哈希校验）
_meta_cache=""
meta() { # meta <repo> <filename> → "size sha256"（空=查不到）
  local repo="$1" fn="$2"
  if [ -z "$_meta_cache" ]; then
    _meta_cache=$(curl -sm 30 "$HF_BASE/api/models/$repo/tree/main?recursive=true" \
      | python -c "
import json,sys
try:
    for f in json.load(sys.stdin):
        l=f.get('lfs') or {}
        if l.get('oid'): print(f['path'], l['size'], l['oid'])
except Exception: pass")
  fi
  awk -v fn="$fn" '$1==fn {print $2, $3}' <<< "$_meta_cache"
}

fetch() { # fetch <url> <out> [repo] [filename] — 断点续传 + 停滞自愈 + sha256 校验
  local url="$1" out="$2" repo="${3:-}" fn="${4:-}" tries=0 meta_sz meta_sha
  if [ -s "$out" ] && [ -n "$fn" ]; then
    read -r meta_sz meta_sha <<< "$(meta "$repo" "$fn")"
    if [ -n "$meta_sha" ] && [ "$(sha256sum "$out" | awk '{print $1}')" = "$meta_sha" ]; then
      echo "[skip] $(basename "$out") 已在（sha256 过）"; return 0
    fi
  elif [ -s "$out" ] && done_ok "$url" "$out"; then
    echo "[skip] $(basename "$out") 已在"; return 0
  fi
  echo "[get] $(basename "$out")"
  while [ $tries -lt 8 ]; do
    curl -fL -C - --retry 3 --retry-delay 5 \
         --speed-time 40 --speed-limit 20000 -o "$out" "$url" && {
      if [ -n "$fn" ]; then
        read -r meta_sz meta_sha <<< "$(meta "$repo" "$fn")"
        if [ -n "$meta_sha" ] && [ "$(sha256sum "$out" | awk '{print $1}')" != "$meta_sha" ]; then
          echo "  sha256 不符（疑似交叉写坏），删掉重下"; rm -f "$out"; tries=$((tries + 1)); continue
        fi
      fi
      return 0
    }
    tries=$((tries + 1)); echo "  重试 $tries（断点续传）…"; sleep 5
  done
  echo "[FAIL] $url"; return 1
}

rc=0
if [ $want_bin -eq 1 ]; then
  ZIP="$VJ_HOME/llama-$LLAMA_BUILD-bin-win-cpu-x64.zip"
  fetch "https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA_BUILD/llama-$LLAMA_BUILD-bin-win-cpu-x64.zip" "$ZIP" || rc=1
  if [ -s "$ZIP" ]; then
    python - "$ZIP" "$BIN_DIR" <<'PY'
import os, sys, zipfile
zip_path, bin_dir = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(zip_path) as z:
    for n in z.namelist():
        if n.endswith(".exe") or n.endswith(".dll"):
            dst = os.path.join(bin_dir, os.path.basename(n))
            with z.open(n) as src, open(dst, "wb") as f:
                f.write(src.read())
            print("[unzip]", os.path.basename(dst))
PY
  fi
fi

if [ $want_models -eq 1 ]; then
  for sz in 2B 4B 8B; do
    repo="Qwen/Qwen3-VL-$sz-Instruct-GGUF"
    fetch "$HF_BASE/$repo/resolve/main/Qwen3VL-$sz-Instruct-Q4_K_M.gguf" \
          "$MODEL_DIR/Qwen3VL-$sz-Instruct-Q4_K_M.gguf" "$repo" \
          "Qwen3VL-$sz-Instruct-Q4_K_M.gguf" || rc=1
    fetch "$HF_BASE/$repo/resolve/main/mmproj-Qwen3VL-$sz-Instruct-F16.gguf" \
          "$MODEL_DIR/mmproj-Qwen3VL-$sz-Instruct-F16.gguf" "$repo" \
          "mmproj-Qwen3VL-$sz-Instruct-F16.gguf" || rc=1
  done
fi

echo "== 完成（rc=$rc）=="
ls -l "$BIN_DIR"/llama-server.exe "$MODEL_DIR"/ 2>/dev/null | tail -10
exit $rc
