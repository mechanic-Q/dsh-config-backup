#!/usr/bin/env bash
# spike #83: WSL 内经 powershell.exe 拉起 Windows Strata 并计时到可响应。
# 在 WSL 内执行：wsl.exe bash /mnt/c/.../scripts/spike-strata-start.sh
# 启动命令 = run-iq3_s.bat 的等价命令 + --host 0.0.0.0（去 --open，免浏览器弹窗）。
set -u
GW=$(ip route show default | awk '/default/ {print $3; exit}')
BASE="http://$GW:8080"
echo "gateway=$GW base=$BASE"

ms() { date +%s%3N; }
t0=$(ms)

powershell.exe -NoProfile -Command "Start-Process -WindowStyle Hidden -FilePath 'E:\AI\LLM\Strata\.venv\Scripts\python.exe' -ArgumentList 'E:\AI\LLM\Strata\serve\server.py','--engine','strata','--config','E:\AI\LLM\Strata\strata-iq3_s.json','--port','8080','--host','0.0.0.0'" >/dev/null 2>&1
echo "launch_issued_ms=$(( $(ms) - t0 ))"

code=000
for i in $(seq 1 300); do
  code=$(curl -s -m 2 -o /dev/null -w '%{http_code}' "$BASE/v1/models" 2>/dev/null || true)
  el=$(( $(ms) - t0 ))
  if [ "$code" = "200" ]; then echo "T_RESPONSIVE_MS=$el"; break; fi
  if [ $((i % 15)) -eq 0 ]; then echo "  poll_${el}ms code=$code"; fi
  sleep 2
done
[ "$code" = "200" ] || { echo "TIMEOUT_AFTER_MS=$(( $(ms) - t0 ))"; exit 1; }

tc0=$(ms)
cc=$(curl -s -m 180 -X POST "$BASE/v1/chat/completions" -H 'Content-Type: application/json' \
  -d '{"model":"strata","messages":[{"role":"user","content":"Say OK"}],"max_tokens":8,"temperature":0}')
echo "T_FIRST_COMPLETION_MS=$(( $(ms) - tc0 ))"
echo "first_body=$(echo "$cc" | head -c 300)"
echo "T_TOTAL_MS=$(( $(ms) - t0 ))"
