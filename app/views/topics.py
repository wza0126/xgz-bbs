# -*- coding: utf-8 -*-
"""话题：发布、详情、回复、置顶、关闭、编辑、删除。"""
from flask import (Blueprint, abort, flash, g, redirect, render_template, request, url_for)

from .. import auth as authm
from .. import db as dbm
from .. import models
from .. import utils
from .files import claim_attachments, form_attach_ids

bp = Blueprint("topics", __name__)


# ---------- 发布 ----------

@bp.route("/b/<int:board_id>/new", methods=["GET", "POST"])
@authm.board_visible_required
@authm.board_role_required("member")
def create(board_id):
    board = g.board
    user = authm.current_user()
    is_leader = authm.is_leader(user, board_id)
    members = models.members_of(board_id)

    if request.method == "POST":
        kind = request.form.get("kind", "discussion")
        if kind not in ("discussion", "notice", "task", "training", "achievement"):
            kind = "discussion"
        if kind in ("notice", "task") and not is_leader:
            abort(403)

        title = (request.form.get("title") or "").strip()
        body = (request.form.get("body") or "").strip()
        is_pinned = 1 if (request.form.get("is_pinned") and kind in ("notice", "task")) else 0

        if len(title) < 2:
            flash("标题太短了，至少 2 个字", "error")
            return _render_new(board, kind, members, is_leader)

        ts = dbm.now_ts()
        topic_id = dbm.execute(
            "INSERT INTO topics (board_id, author_id, kind, title, body, is_pinned,"
            " status, view_count, reply_count, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,'open',0,0,?,?)",
            (board_id, user["id"], kind, title[:120], body, is_pinned, ts, ts))

        assignee_ids = []
        if kind == "task":
            assignee_ids = [int(x) for x in request.form.getlist("assignee_ids") if x.isdigit()]
            assignee_ids = [uid for uid in dict.fromkeys(assignee_ids)
                            if authm.membership(uid, board_id)]
            if not assignee_ids:
                flash("任务至少要指派给一位组员（也可以先建好，稍后在任务卡里补指派）", "warn")
            points = int(request.form.get("points") or 0)
            points = max(0, min(points, 1000))
            due_date = (request.form.get("due_date") or "").strip() or None
            task_id = dbm.execute(
                "INSERT INTO tasks (topic_id, board_id, title, detail, points, due_date,"
                " created_by, status, created_at) VALUES (?,?,?,?,?,?,?,'open',?)",
                (topic_id, board_id, title[:120], body, points, due_date, user["id"], ts))
            for uid in assignee_ids:
                dbm.execute(
                    "INSERT OR IGNORE INTO task_assignees (task_id, user_id, status) VALUES (?,?, 'todo')",
                    (task_id, uid))
            claim_attachments(form_attach_ids(request), user_id=user["id"],
                              attachable_type="topic", attachable_id=topic_id, board_id=board_id)
            if assignee_ids:
                due_txt = f"，请在 {due_date} 前完成" if due_date else ""
                utils.notify(assignee_ids, actor_id=user["id"], kind="assign",
                             summary=f"{user['display_name']} 给你派了任务「{title}」{due_txt}",
                             link=url_for("topics.detail", topic_id=topic_id),
                             board_id=board_id, topic_id=topic_id, task_id=task_id)
            others = [m["id"] for m in members if m["id"] not in assignee_ids]
            utils.notify(others, actor_id=user["id"], kind="topic",
                         summary=f"{board['name']} 新任务：{title}",
                         link=url_for("topics.detail", topic_id=topic_id),
                         board_id=board_id, topic_id=topic_id, task_id=task_id)
            flash("任务已发布" + ("，已通知指派的老师" if assignee_ids else ""), "ok")
        else:
            claim_attachments(form_attach_ids(request), user_id=user["id"],
                              attachable_type="topic", attachable_id=topic_id, board_id=board_id)
            if kind == "notice":
                utils.notify([m["id"] for m in members], actor_id=user["id"], kind="topic",
                             summary=f"{board['name']} 新公告：{title}",
                             link=url_for("topics.detail", topic_id=topic_id),
                             board_id=board_id, topic_id=topic_id)
            flash("发布成功", "ok")

        return redirect(url_for("topics.detail", topic_id=topic_id))

    return _render_new(board, "discussion", members, is_leader)


def _render_new(board, kind, members, is_leader):
    return render_template("topic_new.html", board=board, kind=kind,
                           members=members, is_leader=is_leader)


# ---------- 详情 ----------

@bp.route("/t/<int:topic_id>")
@authm.board_visible_required
@authm.login_required
def detail(topic_id):
    topic = models.topic_detail(topic_id)
    if topic is None:
        abort(404)
    user = authm.current_user()
    dbm.execute("UPDATE topics SET view_count = view_count + 1 WHERE id = ?", (topic_id,))

    posts = models.posts_of(topic_id)
    task = models.task_of_topic(topic_id) if topic["kind"] == "task" else None
    assignees = models.assignees_of(task["id"]) if task else []
    my_assignee = next((a for a in assignees if a["user_id"] == user["id"]), None)
    is_leader = authm.is_leader(user, topic["board_id"])

    topic_atts = utils.attachments_for("topic", [topic_id]).get(topic_id, [])
    post_atts = utils.attachments_for("post", [p["id"] for p in posts])
    children = {}
    top_level = []
    for p in posts:
        if p["parent_id"]:
            children.setdefault(p["parent_id"], []).append(p)
        else:
            top_level.append(p)

    return render_template("topic.html", board=g.board, topic=topic, posts=posts,
                           top_level=top_level, children=children, post_atts=post_atts,
                           topic_atts=topic_atts, task=task, assignees=assignees,
                           my_assignee=my_assignee, is_leader=is_leader,
                           members=models.members_of(topic["board_id"]),
                           is_submission={a["submission_post_id"] for a in assignees
                                          if a["submission_post_id"]})


# ---------- 回复 ----------

@bp.route("/t/<int:topic_id>/reply", methods=["POST"])
@authm.board_visible_required
@authm.board_role_required("member")
def reply(topic_id):
    topic = models.topic_detail(topic_id)
    if topic is None:
        abort(404)
    if topic["status"] == "closed" and not authm.is_leader(authm.current_user(), topic["board_id"]):
        flash("这个话题已经关闭，不能再回复了", "error")
        return redirect(url_for("topics.detail", topic_id=topic_id))

    user = authm.current_user()
    body = (request.form.get("body") or "").strip()
    parent_id = request.form.get("parent_id")
    parent_id = int(parent_id) if (parent_id or "").isdigit() else None
    if parent_id:
        ok = dbm.row("SELECT id FROM posts WHERE id = ? AND topic_id = ?", (parent_id, topic_id))
        if ok is None:
            parent_id = None

    if not body and not form_attach_ids(request):
        flash("写点什么再发吧", "error")
        return redirect(url_for("topics.detail", topic_id=topic_id))

    ts = dbm.now_ts()
    floor = (dbm.scalar("SELECT MAX(floor_no) FROM posts WHERE topic_id = ?", (topic_id,), 0) or 0) + 1
    post_id = dbm.execute(
        "INSERT INTO posts (topic_id, author_id, parent_id, floor_no, body, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (topic_id, user["id"], parent_id, floor, body, ts, ts))

    claim_attachments(form_attach_ids(request), user_id=user["id"],
                      attachable_type="post", attachable_id=post_id, board_id=topic["board_id"])

    dbm.execute(
        "UPDATE topics SET reply_count = reply_count + 1, last_reply_at = ?,"
        " last_reply_uid = ?, updated_at = ? WHERE id = ?",
        (ts, user["id"], ts, topic_id))

    targets = [topic["author_id"]]
    if parent_id:
        prow = dbm.row("SELECT author_id FROM posts WHERE id = ?", (parent_id,))
        if prow:
            targets.append(prow["author_id"])
    targets += utils.users_by_display_names(utils.extract_mentions(body))

    link = url_for("topics.detail", topic_id=topic_id)
    utils.notify(targets, actor_id=user["id"], kind="reply",
                 summary=f"{user['display_name']} 回复了「{topic['title']}」",
                 link=link, board_id=topic["board_id"], topic_id=topic_id)

    flash("回复成功", "ok")
    return redirect(link + f"#p{post_id}")


# ---------- 管理操作 ----------

def _topic_or_404(topic_id):
    topic = models.topic_detail(topic_id)
    if topic is None:
        abort(404)
    return topic


@bp.route("/t/<int:topic_id>/pin", methods=["POST"])
@authm.board_visible_required
@authm.board_role_required("leader")
def pin(topic_id):
    topic = _topic_or_404(topic_id)
    new = 0 if topic["is_pinned"] else 1
    dbm.execute("UPDATE topics SET is_pinned = ? WHERE id = ?", (new, topic_id))
    flash("已置顶" if new else "已取消置顶", "ok")
    return redirect(url_for("topics.detail", topic_id=topic_id))


@bp.route("/t/<int:topic_id>/feature", methods=["POST"])
@authm.board_visible_required
@authm.board_role_required("leader")
def feature(topic_id):
    topic = _topic_or_404(topic_id)
    new = 0 if topic["is_featured"] else 1
    dbm.execute("UPDATE topics SET is_featured = ? WHERE id = ?", (new, topic_id))
    flash("已加精" if new else "已取消加精", "ok")
    return redirect(url_for("topics.detail", topic_id=topic_id))


@bp.route("/t/<int:topic_id>/close", methods=["POST"])
@authm.board_visible_required
@authm.board_role_required("leader")
def close(topic_id):
    topic = _topic_or_404(topic_id)
    new = "open" if topic["status"] == "closed" else "closed"
    dbm.execute("UPDATE topics SET status = ? WHERE id = ?", (new, topic_id))
    if new == "closed":
        dbm.execute("UPDATE tasks SET status = 'closed' WHERE topic_id = ?", (topic_id,))
    else:
        dbm.execute("UPDATE tasks SET status = 'open' WHERE topic_id = ?", (topic_id,))
    flash("话题已关闭" if new == "closed" else "话题已重新开放", "ok")
    return redirect(url_for("topics.detail", topic_id=topic_id))


@bp.route("/t/<int:topic_id>/edit", methods=["GET", "POST"])
@authm.board_visible_required
def edit(topic_id):
    topic = _topic_or_404(topic_id)
    user = authm.current_user()
    is_leader = authm.is_leader(user, topic["board_id"])
    if topic["author_id"] != user["id"] and not is_leader:
        abort(403)

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        body = (request.form.get("body") or "").strip()
        if len(title) < 2:
            flash("标题太短了", "error")
        else:
            dbm.execute("UPDATE topics SET title = ?, body = ?, updated_at = ? WHERE id = ?",
                        (title[:120], body, dbm.now_ts(), topic_id))
            claim_attachments(form_attach_ids(request), user_id=user["id"],
                              attachable_type="topic", attachable_id=topic_id,
                              board_id=topic["board_id"])
            if topic["kind"] == "task":
                points = max(0, min(int(request.form.get("points") or 0), 1000))
                due_date = (request.form.get("due_date") or "").strip() or None
                dbm.execute("UPDATE tasks SET title = ?, detail = ?, points = ?, due_date = ?"
                            " WHERE topic_id = ?", (title[:120], body, points, due_date, topic_id))
            flash("已保存修改", "ok")
            return redirect(url_for("topics.detail", topic_id=topic_id))

    task = models.task_of_topic(topic_id) if topic["kind"] == "task" else None
    return render_template("topic_edit.html", board=g.board, topic=topic, task=task,
                           topic_atts=utils.attachments_for("topic", [topic_id]).get(topic_id, []))


@bp.route("/t/<int:topic_id>/delete", methods=["POST"])
@authm.board_visible_required
@authm.board_role_required("leader")
def delete(topic_id):
    topic = _topic_or_404(topic_id)
    for att in dbm.rows("SELECT * FROM attachments WHERE board_id = ? AND ("
                        "(attachable_type = 'topic' AND attachable_id = ?) OR"
                        "(attachable_type = 'post' AND attachable_id IN"
                        " (SELECT id FROM posts WHERE topic_id = ?)))",
                        (topic["board_id"], topic_id, topic_id)):
        utils.delete_attachment(att)
    dbm.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
    flash("话题已删除", "ok")
    return redirect(url_for("boards.detail", board_id=topic["board_id"]))


@bp.route("/p/<int:post_id>/delete", methods=["POST"])
@authm.login_required
def delete_post(post_id):
    post = dbm.row("SELECT p.*, t.board_id, t.id AS tid FROM posts p JOIN topics t ON t.id = p.topic_id"
                   " WHERE p.id = ?", (post_id,))
    if post is None:
        abort(404)
    user = authm.current_user()
    if post["author_id"] != user["id"] and not authm.is_leader(user, post["board_id"]):
        abort(403)
    for att in dbm.rows("SELECT * FROM attachments WHERE attachable_type = 'post' AND attachable_id = ?",
                        (post_id,)):
        utils.delete_attachment(att)
    dbm.execute("UPDATE posts SET is_deleted = 1, body = '' WHERE id = ?", (post_id,))
    dbm.execute("UPDATE topics SET reply_count = MAX(reply_count - 1, 0) WHERE id = ?", (post["tid"],))
    flash("回复已删除", "ok")
    return redirect(url_for("topics.detail", topic_id=post["tid"]))
