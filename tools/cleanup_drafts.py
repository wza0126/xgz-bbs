# -*- coding: utf-8 -*-
"""清理停留太久的草稿附件（上传了却一直没发出去的文件 / 正文插图）。

为什么需要它：正文插图复用附件体系 —— 图片先以 `attachable_type='draft'` 落盘，
提交表单时才认领到话题/回复上。老师传了图又放弃发帖，那张图就永远停在 draft。
附件区同理会残留这种草稿。

这个脚本只动 `attachable_type='draft'` 且超过 N 天的行，**默认只列不删**：

    python tools/cleanup_drafts.py              # 干跑，看看会清掉什么
    python tools/cleanup_drafts.py --days 3     # 改天数（默认 7 天）
    python tools/cleanup_drafts.py --apply      # 确认后真删（连磁盘文件）

已经认领过的附件（挂在话题/回复上的）永远不会被这个脚本碰到 —— 正文里的图片是安全的。
"""
import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as cfg  # noqa: E402


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def main():
    ap = argparse.ArgumentParser(description="清理过期草稿附件")
    ap.add_argument("--days", type=int, default=7, help="保留多少天以内的草稿（默认 7）")
    ap.add_argument("--apply", action="store_true", help="真的删除；不加这个参数只列出来")
    args = ap.parse_args()

    cutoff = int(time.time()) - args.days * 86400
    con = sqlite3.connect(str(cfg.DB_PATH))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT a.*, u.display_name FROM attachments a JOIN users u ON u.id = a.owner_id"
        " WHERE a.attachable_type = 'draft' AND a.created_at < ? ORDER BY a.id",
        (cutoff,)).fetchall()

    total = sum(r["size_bytes"] for r in rows)
    print(f"数据库：{cfg.DB_PATH}")
    print(f"草稿附件：{len(rows)} 个，共 {human_size(total)}（超过 {args.days} 天没被认领）")
    if not rows:
        print("没有需要清理的，收工。")
        con.close()
        return

    for r in rows:
        when = datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d %H:%M")
        print(f"  #{r['id']:<5} {when}  {r['display_name']:<8} {r['orig_name'][:40]:<42}"
              f"{human_size(r['size_bytes'])}")

    if not args.apply:
        print("\n（干跑模式，什么都没删。确认无误后加 --apply 再跑一次）")
        con.close()
        return

    gone = 0
    for r in rows:
        path = Path(cfg.UPLOAD_DIR) / r["stored_path"]
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            print(f"  ! 文件删不掉（记录照删）：{path} —— {e}")
        con.execute("DELETE FROM attachments WHERE id = ? AND attachable_type = 'draft'",
                    (r["id"],))
        gone += 1
    con.commit()
    con.close()
    print(f"\n已清理 {gone} 个草稿附件，释放 {human_size(total)}。")


if __name__ == "__main__":
    main()
