#!/usr/bin/env bash
# spike #83: WSL 内经 taskkill.exe 跨界停止 Strata（树杀 python server + strata.exe 引擎）。
set -u
GW=$(ip route show default | awk '/default/ {print $3; exit}')

pid=$(netstat.exe -ano 2>/dev/null | grep -E 'TCP.*:8080 .*LISTENING' | awk '{print $NF}' | head -1 | tr -d '\r')
echo "listener_pid=$pid"
if [ -z "$pid" ]; then echo "server-not-running"; exit 1; fi

taskkill.exe /F /T /PID "$pid" 2>&1 | tr -d '\r'
sleep 3

echo "--- verify after kill ---"
netstat.exe -ano 2>/dev/null | grep -E ':8080 .*LISTENING' || echo "no-listener-OK"
tasklist.exe 2>/dev/null | grep -i 'strata\.exe' || echo "engine-gone-OK"
curl -s -m 2 -o /dev/null -w 'wsl-curl:%{http_code}\n' "http://$GW:8080/v1/models" || echo "wsl-curl-refused-OK"
