#!/bin/bash
# ============================================================
#  行知教研吧 · 首次部署（群晖 NAS）
#  用法：bash ~/myproject/setup-bbs.sh
#  做三件事：装依赖 → 建库（可选灌演示数据）→ 启动服务
# ============================================================

set -u

MY=/volume2/homes/wza0126/myproject
DIR="$MY/0/xgz-bbs"
VENV="$MY/venv"

echo "============================================================"
echo "  行知教研吧 · 首次部署"
echo "============================================================"

# 1) 虚拟环境
if [ -f "$VENV/bin/activate" ]; then
    echo "==> 复用已有虚拟环境：$VENV"
else
    PYBIN=$(ls -d /volume2/@appstore/python3*/bin/python3 2>/dev/null | tail -1)
    PYBIN=${PYBIN:-/usr/local/bin/python3}
    echo "==> 新建虚拟环境：$VENV（$PYBIN）"
    "$PYBIN" -m venv "$VENV" || { echo "❌ 建虚拟环境失败"; exit 1; }
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate" || { echo "❌ 激活虚拟环境失败"; exit 1; }
echo "    Python: $(python3 -V 2>&1)"

# 2) 依赖
echo "==> 安装依赖 ..."
python3 -m pip install -q --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
python3 -m pip install -q flask waitress -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && echo "    flask / waitress 就绪"

# 3) 建库
cd "$DIR" || { echo "❌ 找不到 $DIR"; exit 1; }
mkdir -p "$DIR/data"
echo "==> 初始化数据库 ..."
python3 - <<'PY'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path.cwd()))
from app import create_app
app = create_app()
with app.app_context():
    import sqlite3
    from app import db as dbm
    n = dbm.scalar("SELECT COUNT(*) FROM users", (), 0)
    print(f"    已有账号 {n} 个")
PY

# 4) 演示数据（库是空的才灌）
if [ "${1:-}" = "--demo" ]; then
    echo "==> 灌入演示数据 ..."
    python3 tools/seed_demo.py || echo "    （跳过）"
fi

# 5) 启动
echo
bash "$MY/restart-bbs.sh"
