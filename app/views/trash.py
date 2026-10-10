# -*- coding: utf-8 -*-
"""回收站：查看 / 恢复 / 彻底删除被删掉的话题、回复、附件。

进出粒度
  · **话题是整体的**：删话题时它下面的回复、任务、附件一起看不见；
    恢复话题 = 整体回来，不用逐个还原。
  · 回复、附件各自独立进出。

权限
  跟「删除」是同一批人 —— 组长管本组、管理员管全部。不会出现
  「谁都能删、却没人能恢复」的错位。
"""
from flask import (Blueprint, abort, flash, g, redirect, render_template, request,
                   url_for)

from .. import auth as authm
from .. import db as dbm
from .. import models
from .. import utils

bp = Blueprint("trash", __name__)


# ---------- 公共 ----------

def _guard(board_id):
    """能进回收站看/动手的：本组组长或管理员（is_leader 对管理员恒为真）。"""
    user = authm.current_user()
    if not authm.is_leader(user, board_id):
        abort(403)
    return user


def _back(board_id):
    """管理员从后台进来的回后台；组长从课题组进来的回本组回收站。"""
    if request.form.get("back") == "admin":
        return redirect(url_for("trash.admin_trash"))
    return redirect(url_for("trash.board_trash", board_id=board_id))


# ---------- 列表页 ----------

def _render(board_id, scope):
    return render_template(
        "trash.html",
        board=getattr(g, "board", None) if board_id else None,
        scope=scope, board_id=board_id,
        counts=models.trash_counts(board_id),
        topics=models.trash_topics(board_id),
        posts=models.trash_posts(board_id),
        attachments=models.trash_attachments(board_id),
        all_boards=(dbm.rows("SELECT id, name FROM boards ORDER BY id")
                    if board_id is None else None),
    )


@bp.route("/b/<int:board_id>/trash")
@authm.board_visible_required
@authm.board_role_required("leader")
def board_trash(board_id):
    return _render(board_id, "board")


@bp.route("/admin/trash")
@authm.admin_required
def admin_trash():
    return _render(None, "all")


# ---------- 恢复 ----------

@bp.route("/trash/topic/<int:topic_id>/restore", methods=["POST"])
@authm.login_required
def topic_restore(topic_id):
    t = models.topic_detail(topic_id)
    if t is None or not t["is_deleted"]:
        abort(404)
    user = _guard(t["board_id"])
    dbm.execute("UPDATE topics SET is_deleted = 0, deleted_at = NULL, deleted_by = NULL"
                " WHERE id = ?", (topic_id,))
    utils.notify([t["author_id"]], actor_id=user["id"], kind="topic",
                 summary=f"你的话题「{t['title']}」已从回收站恢复",
                 link=url_for("topics.detail", topic_id=topic_id),
                 board_id=t["board_id"], topic_id=topic_id)
    flash(f"话题「{t['title']}」已恢复，它下面的讨论、任务和附件都回来了。", "ok")
    return _back(t["board_id"])


@bp.route("/trash/post/<int:post_id>/restore", methods=["POST"])
@authm.login_required
def post_restore(post_id):
    p = dbm.row("SELECT p.*, t.board_id, t.id AS tid, t.title AS topic_title,"
                " t.is_deleted AS topic_deleted"
                " FROM posts p JOIN topics t ON t.id = p.topic_id WHERE p.id = ?", (post_id,))
    if p is None or not p["is_deleted"]:
        abort(404)
    user = _guard(p["board_id"])
    if p["topic_deleted"]:
        # 话题自己还在回收站里 —— 这条回复跟着话题整体走，先恢复话题
        flash("它所属的话题还在回收站里，先恢复那个话题。", "warn")
        return _back(p["board_id"])
    dbm.execute("UPDATE posts SET is_deleted = 0, deleted_at = NULL, deleted_by = NULL"
                " WHERE id = ?", (post_id,))
    dbm.execute("UPDATE topics SET reply_count = reply_count + 1 WHERE id = ?", (p["tid"],))
    utils.notify([p["author_id"]], actor_id=user["id"], kind="reply",
                 summary=f"你在「{p['topic_title']}」里的回复已恢复到讨论区",
                 link=url_for("topics.detail", topic_id=p["tid"]) + f"#p{post_id}",
                 board_id=p["board_id"], topic_id=p["tid"])
    flash("回复已恢复。要是它原来挂着楼中楼，那些回复也会跟着回到这一层。", "ok")
    return _back(p["board_id"])


@bp.route("/trash/attach/<int:attach_id>/restore", methods=["POST"])
@authm.login_required
def attach_restore(attach_id):
    a = dbm.row("SELECT * FROM attachments WHERE id = ?", (attach_id,))
    if a is None or not a["is_deleted"]:
        abort(404)
    _guard(a["board_id"])
    # 宿主还在，才谈得上恢复；宿主也在回收站的话，恢复宿主时会一并带回来
    if a["attachable_type"] == "topic":
        host_deleted = dbm.scalar("SELECT is_deleted FROM topics WHERE id = ?",
                                  (a["attachable_id"],), 0)
    elif a["attachable_type"] == "post":
        host_deleted = dbm.scalar("SELECT is_deleted FROM posts WHERE id = ?",
                                  (a["attachable_id"],), 0)
    else:
        host_deleted = 0
    if host_deleted:
        flash("它所属的内容还在回收站里，先恢复那一条。", "warn")
        return _back(a["board_id"])
    dbm.execute("UPDATE attachments SET is_deleted = 0, deleted_at = NULL, deleted_by = NULL"
                " WHERE id = ?", (attach_id,))
    flash(f"附件「{a['orig_name']}」已恢复，回到原来的位置。", "ok")
    return _back(a["board_id"])


# ---------- 彻底删除 ----------

@bp.route("/trash/topic/<int:topic_id>/purge", methods=["POST"])
@authm.login_required
def topic_purge(topic_id):
    t = models.topic_detail(topic_id)
    if t is None or not t["is_deleted"]:
        abort(404)
    _guard(t["board_id"])
    n = utils.purge_topic(topic_id)
    flash(f"话题「{t['title']}」已彻底删除，连同它名下 {n} 个附件文件，再也恢复不了。", "ok")
    return _back(t["board_id"])


@bp.route("/trash/post/<int:post_id>/purge", methods=["POST"])
@authm.login_required
def post_purge(post_id):
    p = dbm.row("SELECT p.*, t.board_id FROM posts p JOIN topics t ON t.id = p.topic_id"
                " WHERE p.id = ?", (post_id,))
    if p is None or not p["is_deleted"]:
        abort(404)
    _guard(p["board_id"])
    n = utils.purge_post(post_id)
    flash(f"这条回复已彻底删除" + (f"，连同 {n} 个附件" if n else "") + "。", "ok")
    return _back(p["board_id"])


@bp.route("/trash/attach/<int:attach_id>/purge", methods=["POST"])
@authm.login_required
def attach_purge(attach_id):
    a = dbm.row("SELECT * FROM attachments WHERE id = ?", (attach_id,))
    if a is None or not a["is_deleted"]:
        abort(404)
    _guard(a["board_id"])
    utils.purge_attachment(a)
    flash(f"附件「{a['orig_name']}」已彻底删除。", "ok")
    return _back(a["board_id"])


# ---------- 清空 ----------

def _empty(board_id):
    """把（本组 or 全站的）回收站彻底清空。顺序有讲究：
    先话题（它带着最多的附件一起走），再回复，最后才是单独删的附件。"""
    tb = "" if board_id is None else " AND board_id = ?"
    pb = "" if board_id is None else " AND t.board_id = ?"
    ab = "" if board_id is None else " AND a.board_id = ?"
    a = () if board_id is None else (board_id,)

    n_topics = 0
    for r in dbm.rows(f"SELECT id FROM topics WHERE is_deleted = 1{tb}", a):
        utils.purge_topic(r["id"])
        n_topics += 1

    n_posts = 0
    for r in dbm.rows("SELECT p.id FROM posts p JOIN topics t ON t.id = p.topic_id"
                      f" WHERE p.is_deleted = 1 AND t.is_deleted = 0{pb}", a):
        utils.purge_post(r["id"])
        n_posts += 1

    n_atts = 0
    for r in dbm.rows(
            "SELECT * FROM attachments a WHERE a.is_deleted = 1"
            " AND a.attachable_type IN ('topic', 'post')"
            " AND NOT (a.attachable_type = 'topic' AND EXISTS ("
            "      SELECT 1 FROM topics t2 WHERE t2.id = a.attachable_id AND t2.is_deleted = 1))"
            " AND NOT (a.attachable_type = 'post' AND EXISTS ("
            "      SELECT 1 FROM posts p2 WHERE p2.id = a.attachable_id AND p2.is_deleted = 1))"
            f"{ab}", a):
        utils.purge_attachment(r)
        n_atts += 1

    return n_topics, n_posts, n_atts


@bp.route("/b/<int:board_id>/trash/empty", methods=["POST"])
@authm.board_visible_required
@authm.board_role_required("leader")
def board_empty(board_id):
    t, p, f = _empty(board_id)
    flash(f"本组回收站已清空：彻底删除 {t} 个话题、{p} 条回复、{f} 个附件。", "ok")
    return redirect(url_for("trash.board_trash", board_id=board_id))


@bp.route("/admin/trash/empty", methods=["POST"])
@authm.admin_required
def admin_empty():
    t, p, f = _empty(None)
    flash(f"全站回收站已清空：彻底删除 {t} 个话题、{p} 条回复、{f} 个附件。", "ok")
    return redirect(url_for("trash.admin_trash"))
