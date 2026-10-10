# -*- coding: utf-8 -*-
"""共用查询。视图层尽量只调这里，SQL 集中好维护。"""
from . import db as dbm
from . import kinds as kindsm


def my_boards(user_id, scope="all"):
    """scope: all | mine | public"""
    base = (
        "SELECT b.*, u.display_name AS owner_name,"
        " (SELECT role FROM board_members m WHERE m.board_id = b.id AND m.user_id = ?) AS my_role,"
        " (SELECT COUNT(*) FROM topics t WHERE t.board_id = b.id AND t.is_deleted = 0) AS topic_count,"
        " (SELECT COUNT(*) FROM tasks k WHERE k.board_id = b.id) AS task_count,"
        " (SELECT COUNT(*) FROM board_members m WHERE m.board_id = b.id) AS member_count,"
        " (SELECT MAX(COALESCE(t.last_reply_at, t.created_at)) FROM topics t"
        "    WHERE t.board_id = b.id AND t.is_deleted = 0) AS last_active"
        " FROM boards b JOIN users u ON u.id = b.owner_id"
        " WHERE b.is_archived = 0"
    )
    args = [user_id]
    if scope == "mine":
        base += " AND b.id IN (SELECT board_id FROM board_members WHERE user_id = ?)"
        args.append(user_id)
    elif scope == "public":
        base += " AND b.is_public = 1"
    base += " ORDER BY (last_active IS NULL), last_active DESC, b.id DESC"
    return dbm.rows(base, tuple(args))


def board_stats(board_id):
    r = dbm.row(
        "SELECT"
        " (SELECT COUNT(*) FROM topics WHERE board_id = ? AND is_deleted = 0) AS topics,"
        " (SELECT COUNT(*) FROM tasks k JOIN topics t ON t.id = k.topic_id"
        "    WHERE k.board_id = ? AND t.is_deleted = 0) AS tasks,"
        " (SELECT COUNT(*) FROM attachments WHERE board_id = ? AND is_deleted = 0) AS files,"
        " (SELECT COUNT(*) FROM board_members WHERE board_id = ?) AS members,"
        " (SELECT COUNT(*) FROM task_assignees a JOIN tasks k ON k.id = a.task_id"
        "    JOIN topics t ON t.id = k.topic_id"
        "    WHERE k.board_id = ? AND t.is_deleted = 0) AS assign_total,"
        " (SELECT COUNT(*) FROM task_assignees a JOIN tasks k ON k.id = a.task_id"
        "    JOIN topics t ON t.id = k.topic_id"
        "    WHERE k.board_id = ? AND t.is_deleted = 0 AND a.status IN ('done','confirmed')) AS assign_done",
        (board_id,) * 6,
    )
    d = dict(r) if r else {}
    total = d.get("assign_total") or 0
    d["rate"] = round(d.get("assign_done", 0) * 100 / total) if total else 0
    return d


def members_of(board_id):
    return dbm.rows(
        "SELECT m.role, m.joined_at, u.id, u.display_name, u.subject, u.username"
        " FROM board_members m JOIN users u ON u.id = m.user_id"
        " WHERE m.board_id = ?"
        " ORDER BY CASE m.role WHEN 'leader' THEN 0 ELSE 1 END, m.joined_at, u.id",
        (board_id,),
    )


def board_scoreboard(board_id):
    """成员得分榜：组长确认通过时给的分数。"""
    return dbm.rows(
        "SELECT u.id, u.display_name, u.subject,"
        " COUNT(a.id) AS items,"
        " COALESCE(SUM(a.score), 0) AS total_score,"
        " SUM(CASE WHEN a.status = 'confirmed' THEN 1 ELSE 0 END) AS confirmed,"
        " SUM(CASE WHEN a.status IN ('done') THEN 1 ELSE 0 END) AS pending"
        " FROM task_assignees a"
        " JOIN tasks k ON k.id = a.task_id"
        " JOIN topics t ON t.id = k.topic_id"
        " JOIN users u ON u.id = a.user_id"
        " WHERE k.board_id = ? AND t.is_deleted = 0"
        " GROUP BY u.id, u.display_name, u.subject"
        " ORDER BY total_score DESC, confirmed DESC, u.display_name",
        (board_id,),
    )


def topic_list(board_id, kind=None, sort="reply", page=1, per_page=20, keyword=None):
    # 回收站里的话题不进列表（t.is_deleted = 0）；kind 计数也走这里，口径一致
    where = ["t.board_id = ?", "t.is_deleted = 0"]
    args = [board_id]
    if kind in kindsm.codes():
        where.append("t.kind = ?")
        args.append(kind)
    if keyword:
        where.append("(t.title LIKE ? OR t.body LIKE ?)")
        args += [f"%{keyword}%", f"%{keyword}%"]

    order = {
        "reply": "t.is_pinned DESC, COALESCE(t.last_reply_at, t.created_at) DESC",
        "new": "t.is_pinned DESC, t.created_at DESC",
        "hot": "t.is_pinned DESC, t.reply_count DESC, t.view_count DESC",
    }.get(sort, "t.is_pinned DESC, COALESCE(t.last_reply_at, t.created_at) DESC")

    sql = (
        "SELECT t.*, u.display_name AS author_name,"
        " (SELECT display_name FROM users WHERE id = t.last_reply_uid) AS last_reply_name,"
        " (SELECT k.due_date FROM tasks k WHERE k.topic_id = t.id) AS due_date,"
        " (SELECT COUNT(*) FROM task_assignees a JOIN tasks k ON k.id = a.task_id"
        "    WHERE k.topic_id = t.id) AS assign_total,"
        " (SELECT COUNT(*) FROM task_assignees a JOIN tasks k ON k.id = a.task_id"
        "    WHERE k.topic_id = t.id AND a.status IN ('done','confirmed')) AS assign_done"
        " FROM topics t JOIN users u ON u.id = t.author_id"
        f" WHERE {' AND '.join(where)}"
        f" ORDER BY {order} LIMIT ? OFFSET ?"
    )
    rows = dbm.rows(sql, tuple(args + [per_page, (page - 1) * per_page]))
    total = dbm.scalar(f"SELECT COUNT(*) FROM topics t WHERE {' AND '.join(where)}", tuple(args), 0)
    return rows, total


def topic_detail(topic_id):
    """按 id 取话题（返回里带 is_deleted）。刻意**不过滤**软删：
    详情页、回收站的恢复/彻底删除都要靠它定位，放不放行交给视图层判断。"""
    return dbm.row(
        "SELECT t.*, u.display_name AS author_name, u.subject AS author_subject"
        " FROM topics t JOIN users u ON u.id = t.author_id WHERE t.id = ?",
        (topic_id,),
    )


def posts_of(topic_id):
    return dbm.rows(
        "SELECT p.*, u.display_name, u.subject FROM posts p JOIN users u ON u.id = p.author_id"
        " WHERE p.topic_id = ? AND p.is_deleted = 0 ORDER BY p.floor_no",
        (topic_id,),
    )


def task_of_topic(topic_id):
    return dbm.row(
        "SELECT k.*, u.display_name AS creator_name FROM tasks k JOIN users u ON u.id = k.created_by"
        " WHERE k.topic_id = ?",
        (topic_id,),
    )


def assignees_of(task_id):
    return dbm.rows(
        "SELECT a.*, u.display_name, u.subject,"
        " (SELECT display_name FROM users WHERE id = a.reviewed_by) AS reviewer_name"
        " FROM task_assignees a JOIN users u ON u.id = a.user_id"
        " WHERE a.task_id = ? ORDER BY a.id",
        (task_id,),
    )


def my_tasks(user_id, scope="open"):
    where = ["a.user_id = ?"]
    if scope == "open":
        where.append("a.status IN ('todo','doing','rejected')")
    elif scope == "done":
        where.append("a.status IN ('done','confirmed')")
    where.append("t.status = 'open'")
    where.append("t.is_deleted = 0")
    return dbm.rows(
        "SELECT a.status, a.score, a.review_note, a.submitted_at,"
        " k.title AS task_title, k.due_date, k.points, k.topic_id, k.board_id,"
        " b.name AS board_name, t.title AS topic_title, t.status AS topic_status,"
        " cu.display_name AS creator_name"
        " FROM task_assignees a"
        " JOIN tasks k ON k.id = a.task_id"
        " JOIN topics t ON t.id = k.topic_id"
        " JOIN boards b ON b.id = k.board_id"
        " JOIN users cu ON cu.id = k.created_by"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY (k.due_date IS NULL), k.due_date, k.id",
        (user_id,),
    )


def my_boards_brief(user_id, limit=6):
    return dbm.rows(
        "SELECT b.id, b.name, m.role FROM board_members m JOIN boards b ON b.id = m.board_id"
        " WHERE m.user_id = ? AND b.is_archived = 0 ORDER BY m.joined_at DESC LIMIT ?",
        (user_id, limit),
    )


def latest_topics(limit=6, board_ids=None):
    args = []
    sql = (
        "SELECT t.id, t.title, t.kind, b.name AS board_name, t.created_at,"
        " u.display_name AS author_name"
        " FROM topics t JOIN boards b ON b.id = t.board_id JOIN users u ON u.id = t.author_id"
        " WHERE b.is_archived = 0 AND t.is_deleted = 0"
    )
    if board_ids:
        marks = ",".join("?" * len(board_ids))
        sql += f" AND b.id IN ({marks})"
        args = list(board_ids)
    elif board_ids is not None:
        return []
    sql += " ORDER BY t.created_at DESC LIMIT ?"
    args.append(limit)
    return dbm.rows(sql, tuple(args))


def recent_replies(limit=6, board_ids=None):
    args = []
    sql = (
        "SELECT p.id, p.body, p.created_at, p.topic_id, t.title AS topic_title,"
        " b.name AS board_name, u.display_name AS author_name"
        " FROM posts p JOIN topics t ON t.id = p.topic_id JOIN boards b ON b.id = t.board_id"
        " JOIN users u ON u.id = p.author_id"
        " WHERE p.is_deleted = 0 AND t.is_deleted = 0"
    )
    if board_ids:
        marks = ",".join("?" * len(board_ids))
        sql += f" AND b.id IN ({marks})"
        args = list(board_ids)
    elif board_ids is not None:
        return []
    sql += " ORDER BY p.created_at DESC LIMIT ?"
    args.append(limit)
    return dbm.rows(sql, tuple(args))


# ---------- 回收站 ----------
# 进出粒度：**话题是整体**的 —— 话题被删时，它下面的回复、任务、附件一起不可见；
# 恢复话题即整体回来，不需要逐个还原。回复和附件则各自独立进出。
# board_id=None 表示全站（管理员视角）。

def trash_topics(board_id=None, limit=200):
    sql = (
        "SELECT t.*, u.display_name AS author_name,"
        " (SELECT display_name FROM users WHERE id = t.deleted_by) AS deleter_name,"
        " b.name AS board_name, b.id AS board_id,"
        " (SELECT COUNT(*) FROM posts p WHERE p.topic_id = t.id AND p.is_deleted = 0)"
        "   AS live_replies,"
        " (SELECT COUNT(*) FROM attachments a WHERE a.is_deleted = 0 AND ("
        "    (a.attachable_type = 'topic' AND a.attachable_id = t.id) OR"
        "    (a.attachable_type = 'post' AND a.attachable_id IN"
        "       (SELECT id FROM posts WHERE topic_id = t.id)))) AS live_files"
        " FROM topics t JOIN users u ON u.id = t.author_id JOIN boards b ON b.id = t.board_id"
        " WHERE t.is_deleted = 1"
    )
    args = []
    if board_id is not None:
        sql += " AND t.board_id = ?"
        args.append(board_id)
    sql += " ORDER BY COALESCE(t.deleted_at, t.updated_at) DESC LIMIT ?"
    args.append(limit)
    return dbm.rows(sql, tuple(args))


def trash_posts(board_id=None, limit=300):
    """已删回复。只列**宿主话题还在**的 —— 话题自己也进了回收站时，
    这些回复跟着话题整体走，列出来是重复（恢复话题它们就回来了）。"""
    sql = (
        "SELECT p.*, u.display_name, t.title AS topic_title, t.id AS topic_id,"
        " b.name AS board_name, b.id AS board_id,"
        " (SELECT display_name FROM users WHERE id = p.deleted_by) AS deleter_name,"
        " (SELECT COUNT(*) FROM posts c WHERE c.parent_id = p.id AND c.is_deleted = 0)"
        "   AS live_children"
        " FROM posts p JOIN users u ON u.id = p.author_id"
        " JOIN topics t ON t.id = p.topic_id JOIN boards b ON b.id = t.board_id"
        " WHERE p.is_deleted = 1 AND t.is_deleted = 0"
    )
    args = []
    if board_id is not None:
        sql += " AND t.board_id = ?"
        args.append(board_id)
    sql += " ORDER BY COALESCE(p.deleted_at, p.updated_at) DESC LIMIT ?"
    args.append(limit)
    return dbm.rows(sql, tuple(args))


def trash_attachments(board_id=None, limit=300):
    """已删附件（单独删掉的那种）。宿主话题/回复也进了回收站的，跟着宿主走，不重复列。"""
    sql = (
        "SELECT a.*, u.display_name,"
        " (SELECT display_name FROM users WHERE id = a.deleted_by) AS deleter_name,"
        " b.name AS board_name, b.id AS board_id,"
        " t.title AS topic_title, t.id AS topic_id"
        " FROM attachments a JOIN users u ON u.id = a.owner_id"
        " JOIN boards b ON b.id = a.board_id"
        " LEFT JOIN topics t ON (a.attachable_type = 'topic' AND t.id = a.attachable_id)"
        " WHERE a.is_deleted = 1"
        "   AND a.attachable_type IN ('topic', 'post')"
        "   AND NOT (a.attachable_type = 'topic' AND EXISTS ("
        "        SELECT 1 FROM topics t2 WHERE t2.id = a.attachable_id AND t2.is_deleted = 1))"
        "   AND NOT (a.attachable_type = 'post' AND EXISTS ("
        "        SELECT 1 FROM posts p2 WHERE p2.id = a.attachable_id AND p2.is_deleted = 1))"
    )
    args = []
    if board_id is not None:
        sql += " AND a.board_id = ?"
        args.append(board_id)
    sql += " ORDER BY COALESCE(a.deleted_at, a.created_at) DESC LIMIT ?"
    args.append(limit)
    return dbm.rows(sql, tuple(args))


def trash_counts(board_id=None):
    """回收站三个分组的条数，用于页头汇总与「有没有东西」判断。"""
    a = () if board_id is None else (board_id,)
    b_topic = "" if board_id is None else " AND board_id = ?"
    b_post = "" if board_id is None else " AND t.board_id = ?"
    b_att = "" if board_id is None else " AND a.board_id = ?"
    return {
        "topics": dbm.scalar(
            f"SELECT COUNT(*) FROM topics WHERE is_deleted = 1{b_topic}", a, 0),
        "posts": dbm.scalar(
            "SELECT COUNT(*) FROM posts p JOIN topics t ON t.id = p.topic_id"
            f" WHERE p.is_deleted = 1 AND t.is_deleted = 0{b_post}", a, 0),
        "attachments": dbm.scalar(
            "SELECT COUNT(*) FROM attachments a WHERE a.is_deleted = 1"
            " AND a.attachable_type IN ('topic', 'post')"
            " AND NOT (a.attachable_type = 'topic' AND EXISTS ("
            "      SELECT 1 FROM topics t2 WHERE t2.id = a.attachable_id AND t2.is_deleted = 1))"
            " AND NOT (a.attachable_type = 'post' AND EXISTS ("
            "      SELECT 1 FROM posts p2 WHERE p2.id = a.attachable_id AND p2.is_deleted = 1))"
            f"{b_att}", a, 0),
    }
