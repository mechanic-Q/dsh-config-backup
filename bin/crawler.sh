#!/usr/bin/env bash
# 本地爬虫腿开关：crawler.sh up | down | status
#   up   = 起 Docker Desktop(若没起) → 等引擎就绪 → 起 crawl4ai/searxng → 等 healthy/端口通
#   down = 停容器 → 若无其他容器在跑则退出 Docker Desktop → 确认引擎真关
set -u
CONTAINERS="crawl4ai searxng"
DESKTOP_EXE="/c/Program Files/Docker/Docker/Docker Desktop.exe"
ACTION=${1:-status}
log(){ echo "[crawler] $*"; }
engine_up(){ docker info --format '{{.ServerVersion}}' >/dev/null 2>&1; }

case "$ACTION" in
up)
  start_ts=$SECONDS
  if ! engine_up; then
    log "Docker 引擎未就绪 → 启动 Docker Desktop（最长等 180s）"
    docker desktop start >/dev/null 2>&1 &
    for i in $(seq 1 90); do engine_up && break; sleep 2; done
    if ! engine_up && [ -f "$DESKTOP_EXE" ]; then
      log "CLI 没起来，改用 exe 直接启动"
      "$DESKTOP_EXE" >/dev/null 2>&1 &
      for i in $(seq 1 90); do engine_up && break; sleep 2; done
    fi
    engine_up || { log "失败：Docker Desktop 起不来或引擎 180s 未就绪"; exit 1; }
    log "引擎就绪（耗时 $((SECONDS-start_ts))s）"
  else
    log "Docker 引擎已在运行"
  fi
  docker start $CONTAINERS >/dev/null 2>&1 || { log "容器启动失败"; exit 1; }
  for i in $(seq 1 60); do
    h=$(docker inspect -f '{{.State.Health.Status}}' crawl4ai 2>/dev/null || echo none)
    case "$h" in healthy|none) break;; esac
    sleep 2
  done
  code=000
  for i in $(seq 1 30); do
    code=$(curl -s -o /dev/null -m 3 -w '%{http_code}' 'http://127.0.0.1:28888/search?q=test&format=json')
    [ "$code" = "200" ] && break
    sleep 1
  done
  log "完成（总耗时 $((SECONDS-start_ts))s）"
  docker ps --format '  {{.Names}}  {{.Status}}'
  log "searxng=$code  crawl4ai_health=$(docker inspect -f '{{.State.Health.Status}}' crawl4ai 2>/dev/null)"
  ;;
down)
  docker stop $CONTAINERS >/dev/null 2>&1
  others=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -v -E '^(crawl4ai|searxng)$' | tr '\n' ' ')
  if [ -n "$others" ]; then
    log "容器已停，但还有其他容器在跑（$others）→ 保留 Docker Desktop 不动"
    exit 0
  fi
  log "容器已停 → 退出 Docker Desktop"
  docker desktop stop >/dev/null 2>&1
  if engine_up; then
    taskkill //IM "Docker Desktop.exe" //T //F >/dev/null 2>&1
    taskkill //IM "com.docker.backend.exe" //F >/dev/null 2>&1
  fi
  for i in $(seq 1 60); do engine_up || { log "Docker 引擎已关闭 ✔"; exit 0; }; sleep 1; done
  log "容器已停，但 Docker Desktop 仍在运行（可手动退出托盘图标）"
  ;;
status)
  if engine_up; then echo "[crawler] Docker: running (engine $(docker info --format '{{.ServerVersion}}'))"; else echo "[crawler] Docker: stopped"; fi
  docker ps --format '  {{.Names}}  {{.Status}}' 2>/dev/null
  ;;
*)
  echo "用法: crawler.sh up|down|status"; exit 2;;
esac
