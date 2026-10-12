# -*- coding: utf-8 -*-
"""建库 / 老库升级回归测试（不需要起服务，用临时目录跑，不碰 data/）。

守的是这条命脉：**线上那份老库能不能原地升级、老数据会不会丢**。
背景：schema.sql 末尾有 `CREATE INDEX ... ON topics(board_id, is_deleted, ...)`，
而老库的 topics 表还没有 is_deleted —— 如果 init_db 先跑 schema、后补列，
建索引就会直接 `no such column: is_deleted`，**整站起不来**。
所以 init_db 必须「先补列、再跑 schema」。本脚本就是把这件事钉死。

做法：按当前 schema 还原出「上一个版本的库」（摘掉新增列与新增索引），
灌入旧数据，再用当前代码打开它，逐项验证：
  ① 不再抛 no such column           ② 新增列补齐
  ③ 老数据一行不少、is_deleted 默认 0  ④ 重复启动幂等
  ⑤ 迁移后回收站查询可用、软删/恢复链路通
  ⑥ 新表（如 action_todos）在升级时自动建出来、写入正常

用法：.venv/Scripts/python tools/check_migrate.py
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAIL = []
PASS = []


def check(name, cond, extra=""):
    line = ("  ok  " if cond else " FAIL ") + name + (("  " + str(extra)) if extra else "")
    print(line)
    (PASS if cond else FAIL).append(name)


def make_old_schema() -> str:
    """还原「上一个发布版本」的 schema：
      - topics: 去掉 is_deleted / deleted_at / deleted_by
      - posts:  去掉 deleted_at / deleted_by（**is_deleted 老库本来就有，必须保留**）
      - attachments: 去掉 is_deleted / deleted_at / deleted_by
      - 去掉三个回收站索引
      - 整块去掉 action_todos（待办是后加的新表，老库里本来没有）
    新增列／新增表时，记得同步这里（否则就等于没测到）。
    """
    lines = (ROOT / "app" / "schema.sql").read_text(encoding="utf-8").splitlines()
    out = []
    skip_block = False
    for ln in lines:
        # action_todos 整块（注释 + CREATE TABLE + CREATE INDEX）一起摘掉 ——
        # 单靠关键词过滤会漏掉列定义那些行，剩下的半截建表语句会直接语法错。
        if "行动研究待办事项" in ln:
            skip_block = True
        if skip_block:
            if "idx_todos_order" in ln:
                skip_block = False
            continue
        if "deleted_at" in ln or "deleted_by" in ln:
            continue
        # topics / attachments 的 is_deleted 是新增的；posts 那句注释里带「正文保留」的是老列
        if "is_deleted" in ln and "正文保留" not in ln:
            continue
        if any(t in ln for t in ("idx_topics_deleted", "idx_posts_deleted", "idx_att_deleted")):
            continue
        out.append(ln)
    return "\n".join(out) + "\n"


def main():
    tmp = Path(tempfile.mkdtemp(prefix="bbs_mig_"))
    try:
        db_path = tmp / "bbs.db"

        # ---------- 1. 造一个「上一版」的旧库，灌入旧数据 ----------
        con = sqlite3.connect(str(db_path))
        con.executescript(make_old_schema())
        ts = 1700000000
        con.execute("INSERT INTO users (username,password_hash,display_name,subject,role,created_at)"
                    " VALUES ('old1','x','老用户甲','语文','teacher',?)", (ts,))
        uid = con.execute("SELECT id FROM users WHERE username='old1'").fetchone()[0]
        con.execute("INSERT INTO boards (name,owner_id,created_at) VALUES ('老组',?,?)", (uid, ts))
        bid = con.execute("SELECT id FROM boards WHERE name='老组'").fetchone()[0]
        con.execute("INSERT INTO topics (board_id,author_id,kind,title,body,created_at,updated_at)"
                    " VALUES (?,?,'discussion','老话题标题','老话题正文',?,?)", (bid, uid, ts, ts))
        tid = con.execute("SELECT id FROM topics").fetchone()[0]
        con.execute("INSERT INTO posts (topic_id,author_id,floor_no,body,created_at,updated_at)"
                    " VALUES (?,?,1,'老回复正文',?,?)", (tid, uid, ts, ts))
        con.execute("INSERT INTO attachments (board_id,owner_id,attachable_type,attachable_id,"
                    "orig_name,stored_path,ext,mime,size_bytes,sha256,created_at)"
                    " VALUES (?,?,'topic',?,'老附件.txt','2024/01/u1.txt','txt','text/plain',3,'abc',?)",
                    (bid, uid, tid, ts))
        con.commit()
        cols_before = {r[1] for r in con.execute("PRAGMA table_info(topics)")}
        con.close()
        check("旧库确实没有 topics.is_deleted", "is_deleted" not in cols_before)

        # ---------- 2. 用当前代码打开这个老库（env 必须在 import config 之前设）----------
        os.environ["BBS_DATA_DIR"] = str(tmp)
        os.environ["BBS_SECRET_KEY"] = "check-migrate-not-a-secret"
        sys.path.insert(0, str(ROOT))

        from app import create_app, models, db as dbm       # noqa: E402

        app = create_app()                                   # ← 这里以前会崩
        check("create_app 在老库上不再抛异常", True)

        # ---------- 3. 列补齐 + 老数据完好 ----------
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        for tbl in ("topics", "posts", "attachments"):
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({tbl})")}
            need = {"is_deleted", "deleted_at", "deleted_by"}
            check(f"{tbl} 补齐三列", need <= cols, sorted(need - cols))
        t = con.execute("SELECT * FROM topics WHERE id=?", (tid,)).fetchone()
        check("老话题还在且 is_deleted=0", t is not None and t["is_deleted"] == 0)
        check("老话题正文未被动过", t["body"] == "老话题正文")
        p = con.execute("SELECT * FROM posts WHERE topic_id=?", (tid,)).fetchone()
        check("老回复还在且 is_deleted=0", p is not None and p["is_deleted"] == 0)
        a = con.execute("SELECT * FROM attachments").fetchone()
        check("老附件还在且 is_deleted=0", a is not None and a["is_deleted"] == 0)
        n_c1 = len([r for r in con.execute("PRAGMA table_info(topics)")])
        con.close()

        # ---------- 4. 幂等：再启动一次 ----------
        from app.db import init_db                           # noqa: E402
        init_db(app)
        con = sqlite3.connect(str(db_path))
        n_t = con.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        n_c2 = len([r for r in con.execute("PRAGMA table_info(topics)")])
        con.close()
        check("二次启动幂等（话题数不变）", n_t == 1, n_t)
        check("二次启动列数不变", n_c1 == n_c2, (n_c1, n_c2))

        # ---------- 5. 迁移后回收站查询可用、软删/恢复链路通 ----------
        with app.app_context():
            tc = models.trash_counts()
            check("trash_counts 可执行且为空",
                  tc == {"topics": 0, "posts": 0, "attachments": 0}, tc)
            check("trash_topics 返回空列表", models.trash_topics() == [])
            dbm.execute("UPDATE topics SET is_deleted=1, deleted_at=?, deleted_by=? WHERE id=?",
                        (dbm.now_ts(), uid, tid))
            check("软删后 trash_counts.topics=1", models.trash_counts()["topics"] == 1)
            items, total = models.topic_list(bid)
            check("软删后列表看不到（total=0）", total == 0 and items == [], (total, len(items)))
            dbm.execute("UPDATE topics SET is_deleted=0, deleted_at=NULL, deleted_by=NULL WHERE id=?",
                        (tid,))
            items, total = models.topic_list(bid)
            check("恢复后又能看到", total == 1 and any(r["id"] == tid for r in items), total)

        # ---------- 6. 新表随升级自动建出、能写能查 ----------
        con = sqlite3.connect(str(db_path))
        cols = {r[1] for r in con.execute("PRAGMA table_info(action_todos)")}
        check("升级后 action_todos 表已建出",
              {"id", "content", "due_date", "is_done", "sort_order", "done_at"} <= cols,
              sorted(cols))
        con.close()
        with app.app_context():
            check("todo_list 可用且为空", models.todo_list() == [])
            check("todo_open_count 可用", models.todo_open_count() == 0)
            dbm.execute("INSERT INTO action_todos (content, due_date, is_done, sort_order,"
                        " created_at, updated_at) VALUES ('迁移后加一条','2030-01-01',0,0,?,?)",
                        (dbm.now_ts(), dbm.now_ts()))
            check("迁移后能写入待办", models.todo_open_count() == 1)
            check("待办能按截止日期读出", models.todo_list()[0]["due_date"] == "2030-01-01")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print("  -", f)
        sys.exit(1)


if __name__ == "__main__":
    main()
