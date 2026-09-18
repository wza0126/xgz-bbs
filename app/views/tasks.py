# -*- coding: utf-8 -*-
"""任务：开始做、提交、组长审核打分、指派调整、我的任务。"""
from flask import (Blueprint, abort, flash, redirect, render_template, request, url_for)

import config as cfg
from .. import auth as authm
from .. import db as dbm
from .. import models
from .. import richtext
from .. import utils
from .files import claim_attachments, form_attach_ids

bp = Blueprint("tasks", __name__)


def _task_context(topic_id):
    topic = models.topic_detail(topic_id)
    if topic is None:
        abort(404)
    task = models.task_of_topic(topic_id)
    if task is None:
        abort(404)
    return topic, task


# ---------- 组员操作 ----------

@bp.route("/t/<int:topic_id>/start", methods=["POST"])
@authm.board_visible_required
@authm.board_role_required("member")
def start(topic_id):
    topic, task = _task_context(topic_id)
    user = authm.current_user()
    a = dbm.row("SELECT * FROM task_assignees WHERE task_id = ? AND user_id = ?",
                (task["id"], user["id"]))
    if a is None:
        abort(403)
    if a["status"] in ("done", "confirmed"):
        flash("你已经提交过了", "error")
    else:
        dbm.execute("UPDATE task_assignees SET status = 'doing' WHERE id = ?", (a["id"],))
        flash("已标为进行中", "ok")
    return redirect(url_for("topics.detail", topic_id=topic_id) + "#task")


@bp.route("/t/<int:topic_id>/submit", methods=["POST"])
@authm.board_visible_required
@authm.board_role_required("member")
def submit(topic_id):
    topic, task = _task_context(topic_id)
    user = authm.current_user()
    a = dbm.row("SELECT * FROM task_assignees WHERE task_id = ? AND user_id = ?",
                (task["id"], user["id"]))
    if a is None:
        abort(403)
    if a["status"] == "confirmed":
        flash("组长已经确认通过了，不用再交啦", "warn")
        return redirect(url_for("topics.detail", topic_id=topic_id) + "#task")
    if topic["status"] == "closed":
        flash("这个任务已经关闭", "error")
        return redirect(url_for("topics.detail", topic_id=topic_id) + "#task")

    try:
        body, body_fmt = richtext.parse_body(request.form, max_chars=cfg.BODY_MAX_CHARS)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("topics.detail", topic_id=topic_id) + "#task")
    att_ids = form_attach_ids(request)
    if not body and not att_ids and not a["submission_post_id"]:
        flash("写点说明或者传个附件再提交", "error")
        return redirect(url_for("topics.detail", topic_id=topic_id) + "#task")

    ts = dbm.now_ts()
    if a["submission_post_id"]:
        post_id = a["submission_post_id"]
        exists = dbm.row("SELECT id FROM posts WHERE id = ?", (post_id,))
        if exists is None:
            post_id = None
    else:
        post_id = None

    if post_id is None:
        floor = (dbm.scalar("SELECT MAX(floor_no) FROM posts WHERE topic_id = ?", (topic_id,), 0) or 0) + 1
        post_id = dbm.execute(
            "INSERT INTO posts (topic_id, author_id, parent_id, floor_no, body, body_format,"
            " created_at, updated_at) VALUES (?,?,NULL,?,?,?,?,?)",
            (topic_id, user["id"], floor, body, body_fmt, ts, ts))
        dbm.execute("UPDATE topics SET reply_count = reply_count + 1, last_reply_at = ?,"
                    " last_reply_uid = ?, updated_at = ? WHERE id = ?",
                    (ts, user["id"], ts, topic_id))
    else:
        dbm.execute("UPDATE posts SET body = ?, body_format = ?, updated_at = ? WHERE id = ?",
                    (body, body_fmt, ts, post_id))

    claim_attachments(att_ids, user_id=user["id"], attachable_type="post",
                      attachable_id=post_id, board_id=topic["board_id"])

    dbm.execute(
        "UPDATE task_assignees SET status = 'done', submission_post_id = ?, submitted_at = ?"
        " WHERE id = ?", (post_id, ts, a["id"]))

    utils.notify([task["created_by"]], actor_id=user["id"], kind="submit",
                 summary=f"{user['display_name']} 提交了任务「{task['title']}」，请审核",
                 link=url_for("topics.detail", topic_id=topic_id) + "#task",
                 board_id=topic["board_id"], topic_id=topic_id, task_id=task["id"])
    flash("已提交，等组长审核", "ok")
    return redirect(url_for("topics.detail", topic_id=topic_id) + "#task")


# ---------- 组长操作 ----------

@bp.route("/t/<int:topic_id>/review", methods=["POST"])
@authm.board_visible_required
@authm.board_role_required("leader")
def review(topic_id):
    topic, task = _task_context(topic_id)
    user = authm.current_user()
    assignee_id = request.form.get("assignee_id")
    if not (assignee_id or "").isdigit():
        abort(400)
    a = dbm.row("SELECT * FROM task_assignees WHERE id = ? AND task_id = ?",
                (int(assignee_id), task["id"]))
    if a is None:
        abort(404)

    action = request.form.get("action")
    note = (request.form.get("review_note") or "").strip()[:500]
    link = url_for("topics.detail", topic_id=topic_id) + "#task"
    who = dbm.row("SELECT display_name FROM users WHERE id = ?", (a["user_id"],))["display_name"]

    if action == "confirm":
        raw_score = (request.form.get("score") or "").strip()
        score = None
        if raw_score:
            try:
                score = max(0, min(int(float(raw_score)), 1000))
            except ValueError:
                score = None
        dbm.execute(
            "UPDATE task_assignees SET status = 'confirmed', score = ?, review_note = ?,"
            " reviewed_by = ?, reviewed_at = ? WHERE id = ?",
            (score, note, user["id"], dbm.now_ts(), a["id"]))
        score_txt = f"，得分 {score} 分" if score is not None else ""
        utils.notify([a["user_id"]], actor_id=user["id"], kind="review",
                     summary=f"你的「{task['title']}」已通过{score_txt}",
                     link=link, board_id=topic["board_id"], topic_id=topic_id, task_id=task["id"])
        flash(f"{who} 的提交已确认通过" + (f"，记 {score} 分" if score is not None else ""), "ok")

    elif action == "reject":
        if not note:
            flash("打回要写明原因，不然人家不知道怎么改", "error")
            return redirect(link)
        dbm.execute(
            "UPDATE task_assignees SET status = 'rejected', review_note = ?,"
            " reviewed_by = ?, reviewed_at = ? WHERE id = ?",
            (note, user["id"], dbm.now_ts(), a["id"]))
        utils.notify([a["user_id"]], actor_id=user["id"], kind="review",
                     summary=f"你的「{task['title']}」被打回：{note[:60]}",
                     link=link, board_id=topic["board_id"], topic_id=topic_id, task_id=task["id"])
        flash(f"已打回 {who} 的提交", "ok")
    else:
        abort(400)

    return redirect(link)


@bp.route("/t/<int:topic_id>/assign", methods=["POST"])
@authm.board_visible_required
@authm.board_role_required("leader")
def assign(topic_id):
    topic, task = _task_context(topic_id)
    user = authm.current_user()
    wanted = [int(x) for x in request.form.getlist("assignee_ids") if x.isdigit()]
    wanted = [uid for uid in dict.fromkeys(wanted) if authm.membership(uid, topic["board_id"])]

    current = {r["user_id"] for r in dbm.rows(
        "SELECT user_id FROM task_assignees WHERE task_id = ?", (task["id"],))}
    added = [u for u in wanted if u not in current]
    removed = [u for u in current if u not in wanted]

    for uid in removed:
        dbm.execute("DELETE FROM task_assignees WHERE task_id = ? AND user_id = ?", (task["id"], uid))
    for uid in added:
        dbm.execute("INSERT INTO task_assignees (task_id, user_id, status) VALUES (?,?, 'todo')",
                    (task["id"], uid))

    if added:
        due_txt = f"，请在 {task['due_date']} 前完成" if task["due_date"] else ""
        utils.notify(added, actor_id=user["id"], kind="assign",
                     summary=f"{user['display_name']} 给你派了任务「{task['title']}」{due_txt}",
                     link=url_for("topics.detail", topic_id=topic_id) + "#task",
                     board_id=topic["board_id"], topic_id=topic_id, task_id=task["id"])
    flash(f"指派已更新（新增 {len(added)} 人，移除 {len(removed)} 人）", "ok")
    return redirect(url_for("topics.detail", topic_id=topic_id) + "#task")


# ---------- 我的任务 ----------

@bp.route("/my/tasks")
@authm.login_required
def my_tasks():
    user = authm.current_user()
    scope = request.args.get("scope", "open")
    if scope not in ("open", "done", "all"):
        scope = "open"
    items = models.my_tasks(user["id"], scope)

    today = utils.local_today()
    overdue = sum(1 for r in items if r["due_date"] and r["due_date"] < today
                  and r["status"] in ("todo", "doing", "rejected"))
    scored = [r["score"] for r in items if r["score"] is not None]
    summary = {
        "total": len(items),
        "overdue": overdue,
        "done": sum(1 for r in items if r["status"] in ("done", "confirmed")),
        "score": sum(scored),
    }
    return render_template("my_tasks.html", items=items, scope=scope, summary=summary,
                           today=today)
