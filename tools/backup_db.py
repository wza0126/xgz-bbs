# -*- coding: utf-8 -*-
"""安全备份 SQLite。

WAL 模式下**不能直接复制 bbs.db**（可能拿到不一致的快照，还会漏掉 -wal 里的最新事务），
必须用 `VACUUM INTO` 让 SQLite 自己导出一份完整、一致的副本。

用法（在 NAS 上）：
    python3 tools/backup_db.py                 # 备份到 data/backups/bbs-YYYY-MM-DD_HHMM.db
    python3 tools/backup_db.py --keep 14       # 顺便只保留最近 14 份
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config as cfg  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=0, help="只保留最近 N 份备份（0 = 不清理）")
    a = ap.parse_args()

    if not cfg.DB_PATH.exists():
        print(f"数据库不存在：{cfg.DB_PATH}")
        return 1

    cfg.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M")
    dst = cfg.BACKUP_DIR / f"bbs-{stamp}.db"
    if dst.exists():
        dst = cfg.BACKUP_DIR / f"bbs-{stamp}-{int(time.time()) % 1000}.db"

    src = sqlite3.connect(str(cfg.DB_PATH))
    try:
        src.execute("VACUUM INTO '%s'" % str(dst).replace("'", "''"))
    finally:
        src.close()

    # 立刻回读一遍，确认这份备份真的能用（不是空的、没损坏）
    chk = sqlite3.connect(str(dst))
    try:
        users = chk.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        topics = chk.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        integrity = chk.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        chk.close()

    print(f"已备份 → {dst}  ({dst.stat().st_size / 1024:.0f} KB)")
    print(f"回读校验：users={users} topics={topics} integrity_check={integrity}")

    if a.keep > 0:
        olds = sorted(cfg.BACKUP_DIR.glob("bbs-*.db"))[:-a.keep]
        for p in olds:
            p.unlink()
            print(f"清理旧备份 {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
