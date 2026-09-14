# -*- coding: utf-8 -*-
"""
灌演示数据：6 位老师 + 3 个课题组 + 若干话题/任务/回复/附件。
用法（在项目根目录）：
    .venv\\Scripts\\python.exe tools\\seed_demo.py
    .venv\\Scripts\\python.exe tools\\seed_demo.py --reset    # 先清空业务数据再灌
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("BBS_DATA_DIR", str(BASE / "data"))

from app import create_app                      # noqa: E402
from app import db as dbm                       # noqa: E402
from app.auth import hash_password              # noqa: E402

TZ = timezone(timedelta(hours=8))
PW = "123456"

USERS = [
    ("admin", "系统管理员", "信息中心", "admin"),
    ("zhangls", "张老师", "信息科技", "teacher"),
    ("wuls", "吴老师", "信息科技", "teacher"),
    ("lils", "李老师", "信息科技", "teacher"),
    ("chenls", "陈老师", "信息科技", "teacher"),
    ("wangls", "王老师", "数学", "teacher"),
    ("zhouls", "周老师", "信息科技", "teacher"),
]


def ts(days=0, hours=0, minutes=0):
    base = datetime.now(tz=TZ).replace(second=0, microsecond=0)
    return int((base + timedelta(days=days, hours=hours, minutes=minutes)).timestamp())


def day(offset):
    return (datetime.now(tz=TZ) + timedelta(days=offset)).strftime("%Y-%m-%d")


def wipe():
    for t in ("reactions", "notifications", "attachments", "task_assignees", "tasks",
              "posts", "topics", "board_members", "boards"):
        dbm.execute(f"DELETE FROM {t}")
    dbm.execute("DELETE FROM users WHERE username != 'admin'")
    # 重置自增计数器：否则反复 --reset 会让 id 一路漂移，
    # 依赖固定 id 的脚本（如 smoke_test）就会全部失效。
    dbm.execute("DELETE FROM sqlite_sequence")
    print("  已清空业务数据（自增 id 已重置）")


def add_attachment(board_id, a_type, a_id, owner_id, filename, content, dayoffset=-1):
    from flask import current_app
    upload_root = Path(current_app.config["UPLOAD_DIR"])
    rel_dir = Path("2026") / "09"
    (upload_root / rel_dir).mkdir(parents=True, exist_ok=True)
    import uuid
    ext = filename.rsplit(".", 1)[-1].lower()
    stored = uuid.uuid4().hex + "." + ext
    (upload_root / rel_dir / stored).write_bytes(content)
    dbm.execute(
        "INSERT INTO attachments (owner_id, board_id, attachable_type, attachable_id,"
        " orig_name, stored_path, ext, mime, size_bytes, sha256, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (owner_id, board_id, a_type, a_id, filename, str(rel_dir / stored).replace("\\", "/"),
         ext, "text/plain", len(content), "", ts(days=dayoffset)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="先清空业务数据")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        if args.reset:
            wipe()

        if dbm.scalar("SELECT COUNT(*) FROM boards", (), 0) > 0:
            print("已经有数据了。要重新灌请加 --reset。")
            return

        # ---------- 用户 ----------
        uid = {}
        for username, name, subject, role in USERS:
            row = dbm.row("SELECT id FROM users WHERE username = ?", (username,))
            if row:
                uid[username] = row["id"]
                continue
            uid[username] = dbm.execute(
                "INSERT INTO users (username, password_hash, display_name, subject, role, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (username, hash_password(PW), name, subject, role, ts(days=-180)))
        print(f"  用户 {len(uid)} 个（密码统一 {PW}）")

        # ---------- 课题组 ----------
        boards = {}

        def mk_board(name, desc, kind, owner, member_role_pairs, public=0, created=-200):
            bid = dbm.execute(
                "INSERT INTO boards (name, description, kind, is_public, owner_id, created_at)"
                " VALUES (?,?,?,?,?,?)", (name, desc, kind, public, uid[owner], ts(days=created)))
            for uname, role in member_role_pairs:
                dbm.execute(
                    "INSERT OR REPLACE INTO board_members (board_id, user_id, role, joined_at)"
                    " VALUES (?,?,?,?)", (bid, uid[uname], role, ts(days=created)))
            boards[owner + str(bid)] = bid
            return bid

        b1 = mk_board(
            "核心素养导向的高中信息技术项目式学习实践研究",
            "2025 年度江苏省教育科学规划课题。本组负责课例设计、数据采集与阶段性成果整理，每两周一次集中研讨。",
            "project", "zhangls",
            [("zhangls", "leader"), ("wuls", "member"), ("lils", "member"),
             ("chenls", "member"), ("wangls", "member")])
        b2 = mk_board(
            "基于 Python 的高中算法思维培养路径研究",
            "正在准备中期材料，需提交教学案例 3 篇、学生作品集与课堂观察记录。",
            "project", "zhouls",
            [("zhouls", "leader"), ("wuls", "member"), ("wangls", "member"), ("chenls", "member")],
            created=-150)
        b3 = mk_board(
            "信息组日常教研 · 公开课与集体备课",
            "组内通知、公开课安排、集体备课资料共享。",
            "general", "lils",
            [("lils", "leader"), ("wuls", "member"), ("zhangls", "member"), ("chenls", "member")],
            public=1, created=-120)
        print("  课题组 3 个")

        # ---------- 话题 ----------
        def mk_topic(board_id, author, kind, title, body, created, pinned=0,
                     featured=0, lru=None):
            tid = dbm.execute(
                "INSERT INTO topics (board_id, author_id, kind, title, body, is_pinned, is_featured,"
                " status, view_count, reply_count, last_reply_at, last_reply_uid, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,'open',?,0,?,?,?,?)",
                (board_id, uid[author], kind, title, body, pinned, featured,
                 0, None, None, ts(days=created), ts(days=created)))
            return tid

        def mk_post(topic_id, author, body, floor, created, parent=None):
            pid = dbm.execute(
                "INSERT INTO posts (topic_id, author_id, parent_id, floor_no, body, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (topic_id, uid[author], parent, floor, body, ts(days=created), ts(days=created)))
            return pid

        def touch(topic_id, last_uid, last_days):
            dbm.execute(
                "UPDATE topics SET reply_count = (SELECT COUNT(*) FROM posts WHERE topic_id = ?),"
                " last_reply_at = ?, last_reply_uid = ?, view_count = ? WHERE id = ?",
                (topic_id, ts(days=last_days), uid[last_uid],
                 40 + abs(hash(str(topic_id))) % 220, topic_id))

        # --- 课题一：任务「撰写课例初稿」---
        t1 = mk_topic(b1, "zhangls", "task", "撰写《项目式学习课例》初稿",
                      "按新课程标准，每人设计 1 个 2 课时的项目式学习课例。\n"
                      "需包含：驱动性问题、项目拆解、学生活动设计、评价量规（rubric）、"
                      "可能的学生困难与应对。\n\n请提交 Word 文档，文件名格式「姓名-课例名」。",
                      created=-8, pinned=1)
        task1 = dbm.execute(
            "INSERT INTO tasks (topic_id, board_id, title, detail, points, due_date, created_by,"
            " status, created_at) VALUES (?,?,?,?,?,?,?,'open',?)",
            (t1, b1, "撰写《项目式学习课例》初稿",
             "每人 1 个 2 课时课例，含评价量规。", 10, day(3), uid["zhangls"], ts(days=-8)))

        p_zhang = mk_post(t1, "zhangls",
                          "附件是课例模板和评价量规示例，大家照着写。有问题的直接在本帖回复。",
                          1, -8)
        add_attachment(b1, "topic", t1, uid["zhangls"], "课例模板与评价量规示例.txt",
                       "【课例模板】\n一、驱动性问题\n二、项目拆解\n三、学生活动设计\n四、评价量规\n五、预设困难与应对\n".encode("utf-8"))

        p_li = mk_post(t1, "lils", "我的课例已按量规改完第二版，见附件。", 2, -4)
        add_attachment(b1, "post", p_li, uid["lils"], "李-校园植物识别小程序.txt",
                       "驱动性问题：如何让不会编程的同学也能用程序识别校园植物？\n".encode("utf-8"))
        mk_post(t1, "zhangls", "收到，量规部分做得很好，已确认通过。", 3, -4, parent=p_li)

        p_wu = mk_post(t1, "wuls",
                       "我先交了。第 2 课时我打算让学生自己定评价维度，但是怕课堂失控，"
                       "有没有更好的做法？", 4, -2)
        mk_post(t1, "zhangls", "可以先把维度做成半成品清单，学生只做勾选和补充 —— "
                               "既给了支架，又保留了选择权。", 5, -2, parent=p_wu)
        add_attachment(b1, "post", p_wu, uid["wuls"], "吴-数据驱动的垃圾分类项目.txt",
                       "驱动性问题：学校垃圾桶的分类准确率能提高多少？\n".encode("utf-8"))
        touch(t1, "zhangls", -2)

        for uname, status, score, note, sub in (
                ("lils", "confirmed", 9, "量规部分做得很扎实，可以进课例集。", p_li),
                ("wuls", "done", None, "", p_wu),
                ("chenls", "todo", None, "", None)):
            dbm.execute(
                "INSERT INTO task_assignees (task_id, user_id, status, submission_post_id,"
                " submitted_at, reviewed_by, reviewed_at, score, review_note)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (task1, uid[uname], status, sub, ts(days=-2) if sub else None,
                 uid["zhangls"] if status == "confirmed" else None,
                 ts(days=-3) if status == "confirmed" else None, score, note))

        # --- 课题一：公告 ---
        t2 = mk_topic(b1, "zhangls", "notice", "关于 10 月中期检查的材料清单",
                      "1. 课例初稿 3 篇（已交 2 篇）\n2. 学生问卷原始数据\n3. 课堂观察量表\n"
                      "4. 阶段性成果小结 1 份\n\n请各位在本月底前把手上那部分补齐。",
                      created=-5)
        mk_post(t2, "chenls", "收到，量表我这边周末前给您。", 1, -4)
        mk_post(t2, "wangls", "问卷数据我看看能不能从教务那边导一份。", 2, -3)
        touch(t2, "wangls", -3)

        # --- 课题一：讨论 ---
        t3 = mk_topic(b1, "wuls", "discussion",
                      "课例框架讨论：是先给支架还是先放手让学生试？",
                      "我这两个班差别挺大，实验班放手能做，普通班一放手就散了。大家怎么处理这个度的？",
                      created=-6, featured=1)
        mk_post(t3, "lils", "我一般先给一节课的示范，第二节课再放手，效果稳一点。", 1, -6)
        mk_post(t3, "chenls", "同感，而且小组里要指定一个「技术负责人」，不然全卡在环境问题上。", 2, -5)
        mk_post(t3, "zhangls", "这个点很关键，可以写进我们下一版的教学流程里。@吴老师 你整理一下？",
                3, -2)
        touch(t3, "zhangls", -2)

        # --- 课题一：任务「提交问卷原始数据」---
        t4 = mk_topic(b1, "zhangls", "task", "提交学生问卷原始数据",
                      "把各班问卷的原始数据（含班级、人数、每题选项分布）整理成 Excel 交上来，"
                      "文件名写清楚是哪个班。", created=-3)
        task4 = dbm.execute(
            "INSERT INTO tasks (topic_id, board_id, title, detail, points, due_date, created_by,"
            " status, created_at) VALUES (?,?,?,?,?,?,?,'open',?)",
            (t4, b1, "提交学生问卷原始数据", "按班整理成 Excel。", 5, day(7),
             uid["zhangls"], ts(days=-3)))
        for uname in ("wuls", "lils", "chenls", "wangls"):
            dbm.execute(
                "INSERT INTO task_assignees (task_id, user_id, status, submitted_at)"
                " VALUES (?,?,?,?)",
                (task4, uid[uname], "doing" if uname == "chenls" else "todo", None))
        mk_post(t4, "chenls", "我这边数据已经拿到手了，正在核对异常值。", 1, -1)
        touch(t4, "chenls", -1)

        # --- 课题二 ---
        t5 = mk_topic(b2, "zhouls", "task", "整理中期教学案例 3 篇",
                      "从中期报告里挑出 3 个有代表性的教学案例，按统一格式整理，"
                      "每篇配 2 张课堂照片。", created=-4)
        task5 = dbm.execute(
            "INSERT INTO tasks (topic_id, board_id, title, detail, points, due_date, created_by,"
            " status, created_at) VALUES (?,?,?,?,?,?,?,'open',?)",
            (t5, b2, "整理中期教学案例 3 篇", "3 个案例 + 照片。", 8, day(9),
             uid["zhouls"], ts(days=-4)))
        for uname in ("wuls", "wangls", "chenls"):
            dbm.execute(
                "INSERT INTO task_assignees (task_id, user_id, status) VALUES (?,?, 'todo')",
                (task5, uid[uname]))
        mk_post(t5, "wangls", "我负责的那个班案例已经有两张能用的照片了。", 1, -2)
        touch(t5, "wangls", -2)

        t6 = mk_topic(b2, "zhouls", "discussion", "中期报告的排版统一一下",
                      "上次交上去格式五花八门，这次统一用同一个样式：小四号宋体，1.5 倍行距。",
                      created=-7)
        mk_post(t6, "chenls", "收到。", 1, -6)
        touch(t6, "chenls", -6)

        # --- 课题三 ---
        t7 = mk_topic(b3, "lils", "notice", "本周四第 3 节公开课（吴老师）",
                      "内容：《算法及其实现》第 2 课时。地点：机房 2。没课的老师都来听一下，"
                      "听完现场评课 15 分钟。", created=-2, pinned=1)
        mk_post(t7, "zhangls", "收到，我来。", 1, -2)
        mk_post(t7, "chenls", "我也到。", 2, -1)
        touch(t7, "chenls", -1)

        t8 = mk_topic(b3, "wuls", "discussion", "机房 2 的 Python 环境统一一下版本",
                      "现在有 3.9 和 3.12 两个版本，学生作业交上来跑不通，建议统一到 3.12。",
                      created=-1)
        touch(t8, "wuls", -1)

        n_topics = dbm.scalar("SELECT COUNT(*) FROM topics", (), 0)
        n_tasks = dbm.scalar("SELECT COUNT(*) FROM tasks", (), 0)
        n_files = dbm.scalar("SELECT COUNT(*) FROM attachments", (), 0)
        print(f"  话题 {n_topics} 个 / 任务 {n_tasks} 个 / 附件 {n_files} 个")

        # ---------- 演示通知 ----------
        from app import utils
        utils.notify([uid["wuls"]], actor_id=uid["zhangls"], kind="assign",
                     summary="张老师 指派给你一项新任务「提交学生问卷原始数据」",
                     link=f"/t/{t4}", board_id=b1, topic_id=t4, task_id=task4)
        utils.notify([uid["chenls"]], actor_id=uid["zhangls"], kind="assign",
                     summary="张老师 指派给你一项新任务「撰写《项目式学习课例》初稿」",
                     link=f"/t/{t1}", board_id=b1, topic_id=t1, task_id=task1)

        print()
        print("  演示账号（密码统一 %s）：" % PW)
        print("    管理员   admin   / admin123（仅首次建库的初始值，--reset 不会改动已存在的 admin）")
        print("    课题组长 zhangls 张老师")
        print("    组员     wuls 吴老师 · lils 李老师 · chenls 陈老师 · wangls 王老师 · zhouls 周老师")
        print()


if __name__ == "__main__":
    main()
