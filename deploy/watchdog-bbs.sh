#!/bin/bash
# ============================================================
#  行知教研吧 · 守护脚本（群晖「计划任务」专用）
#  幂等：服务已在运行则直接退出；没运行则自动启动
#  适合两种触发：
#    1) 开机自启  —— NAS 重启后自动把服务拉起来
#    2) 每 5 分钟  —— 服务意外挂掉后自动重启
# ============================================================

MY=/volume2/homes/wza0126/myproject
PORT=8009

cd "$MY" || exit 1

ALIVE=$(netstat -tlnp 2>/dev/null | awk -v p=":$PORT" 'index($4, p) > 0 {print 1}' | head -1)

if [ "$ALIVE" = "1" ]; then
    exit 0
fi

cd "$MY/0/xgz-bbs" || exit 1
source "$MY/venv/bin/activate" 2>/dev/null || true
mkdir -p "$MY/logs"
nohup python3 server.py --port $PORT --data-dir "$MY/0/xgz-bbs/data" >> "$MY/logs/bbs.log" 2>&1 &

echo "[$(date '+%F %T')] 检测到服务未运行，已自动启动 (PID $!)"
