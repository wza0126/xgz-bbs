#!/bin/bash
# ============================================================
#  行知教研吧 · 重启脚本（群晖 NAS）
#  用法：bash ~/myproject/restart-bbs.sh
#  说明：用 netstat 精确定位监听 8009 的进程来停止（不依赖 pgrep）
# ============================================================

set -u

MY="$HOME/myproject"
[ -d "$MY" ] || MY=/volume2/homes/wza0126/myproject

PORT=8009
DIR="$MY/0/xgz-bbs"
LOG_DIR="$MY/logs"
LOG="$LOG_DIR/bbs.log"
mkdir -p "$LOG_DIR"

if [ ! -d "$DIR" ]; then
    echo "❌ 找不到程序目录：$DIR"
    exit 1
fi

find_pids() {
    netstat -tlnp 2>/dev/null | awk -v p=":$PORT" 'index($4, p) > 0 {print $NF}' | sed 's#/.*##' | grep -E '^[0-9]+$' | sort -u
}

echo "==> 停止旧服务 ..."
PIDS=$(find_pids)
if [ -n "$PIDS" ]; then
    echo "    监听 $PORT 的进程: $PIDS"
    for p in $PIDS; do kill "$p" 2>/dev/null; done
    sleep 2
fi
pkill -f "0/xgz-bbs/server.py" 2>/dev/null
sleep 1
PIDS=$(find_pids)
for p in $PIDS; do kill -9 "$p" 2>/dev/null; done
sleep 1

PIDS=$(find_pids)
if [ -n "$PIDS" ]; then
    echo "❌ 端口 $PORT 仍被占用（$PIDS），停止失败"
    exit 1
fi
echo "    已停止"

echo "==> 启动新服务 ..."
cd "$DIR" || exit 1
source "$MY/venv/bin/activate" 2>/dev/null || true
nohup python3 server.py --port $PORT --data-dir "$DIR/data" >> "$LOG" 2>&1 &
NEW_PID=$!
echo "    新进程 PID: $NEW_PID"

# 轮询等待：冷启动（首次导入 + waitress 起监听）可能明显超过 3 秒，
# 固定 sleep 会把「启动慢」误判成「启动失败」。
CODE=000
for i in $(seq 1 30); do
    CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$PORT/healthz" 2>/dev/null)
    [ "$CODE" = "200" ] && break
    sleep 1
done

if [ "$CODE" = "200" ]; then
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    echo "==> ✅ 服务已恢复（HTTP 200，用时约 ${i}s）"
    echo "    校内访问：http://${IP:-192.168.10.201}:$PORT"
else
    echo "==> ❌ 服务未正常启动（HTTP $CODE，已等待 ${i}s），最近日志："
    tail -n 25 "$LOG" 2>/dev/null
    exit 1
fi
