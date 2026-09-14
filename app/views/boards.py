# -*- coding: utf-8 -*-
"""课题组广场、课题组详情、成员管理、材料打包下载。"""
from flask import (Blueprint, abort, flash, g, redirect, render_template, request,
                   send_file, url_for)

from .. import auth as authm
from .. import db as dbm
from .. import models
from .. import utils

bp = Blueprint("boards", __name__)


@bp.route("/plaza")
@authm.login_required
def home():
    user = authm.current_user()
    scope = request.args.get("scope", "all")
    if scope not in ("all", "mine", "public"):
        scope = "all"
    boards = models.my_boards(user["id"], scope)

    mine_ids = [r["id"] for r in models.my_boards_brief(user["id"], limit=200)]
    my_tasks = models.my_tasks(user["id"], "open")[:6]
    replies = models.recent_replies(6, mine_ids)
    latest = models.latest_topics(6, mine_ids)

    return render_template("index.html", boards=boards, scope=scope,
                           my_tasks=my_tasks, replies=replies, latest=latest)


@bp.route("/b/<int:board_id>")
@authm.board_visible_required
def detail(board_id):
    board = g.board
    user = authm.current_user()
    kind = request.args.get("kind") or None
    sort = request.args.get("sort", "reply")
    page = max(int(request.args.get("page", 1) or 1), 1)
    per_page = 20

    topics, total = models.topic_list(board_id, kind=kind, sort=sort, page=page, per_page=per_page)
    pages = max((total + per_page - 1) // per_page, 1)
    stats = models.board_stats(board_id)
    members = models.members_of(board_id)
    is_leader = authm.is_leader(user, board_id)

    return render_template("board.html", board=board, topics=topics, kind=kind, sort=sort,
                           page=page, pages=pages, total=total, stats=stats,
                           members=members, is_leader=is_leader,
                           owner_name=dbm.scalar("SELECT display_name FROM users WHERE id = ?",
                                                 (board["owner_id"],), ""),
                           scoreboard=models.board_scoreboard(board_id) if is_leader else None)


@bp.route("/b/new", methods=["GET", "POST"])
@authm.login_required
def create():
    user = authm.current_user()
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip()
        kind = request.form.get("kind", "project")
        is_public = 1 if request.form.get("is_public") else 0
        if kind not in ("project", "general"):
            kind = "project"
        if len(name) < 2:
            flash("课题组名称至少 2 个字", "error")
        else:
            ts = dbm.now_ts()
            board_id = dbm.execute(
                "INSERT INTO boards (name, description, kind, is_public, owner_id, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (name[:80], description[:500], kind, is_public, user["id"], ts))
            dbm.execute(
                "INSERT INTO board_members (board_id, user_id, role, joined_at) VALUES (?,?,?,?)",
                (board_id, user["id"], "leader", ts))
            flash(f"课题组「{name}」已创建，你是组长", "ok")
            return redirect(url_for("boards.detail", board_id=board_id))

    return render_template("board_new.html")


@bp.route("/b/<int:board_id>/settings", methods=["GET", "POST"])
@authm.board_visible_required
@authm.board_role_required("leader")
def settings(board_id):
    board = g.board
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip()
        kind = request.form.get("kind", board["kind"])
        is_public = 1 if request.form.get("is_public") else 0
        is_archived = 1 if request.form.get("is_archived") else 0
        if len(name) < 2:
            flash("名称至少 2 个字", "error")
        else:
            dbm.execute(
                "UPDATE boards SET name = ?, description = ?, kind = ?, is_public = ?,"
                " is_archived = ? WHERE id = ?",
                (name[:80], description[:500], kind, is_public, is_archived, board_id))
            flash("课题组设置已保存", "ok")
            return redirect(url_for("boards.settings", board_id=board_id))

    candidates = dbm.rows(
        "SELECT id, username, display_name, subject FROM users"
        " WHERE is_active = 1 AND id NOT IN (SELECT user_id FROM board_members WHERE board_id = ?)"
        " ORDER BY display_name", (board_id,))
    return render_template("board_settings.html", board=board, candidates=candidates,
                           members=models.members_of(board_id))


@bp.route("/b/<int:board_id>/members/add", methods=["POST"])
@authm.board_visible_required
@authm.board_role_required("leader")
def member_add(board_id):
    raw = (request.form.get("who") or "").strip()
    if not raw:
        flash("请填写要加入的老师", "error")
        return redirect(url_for("boards.settings", board_id=board_id))

    user_id = None
    if raw.isdigit():
        user_id = int(raw)
    else:
        row = dbm.row("SELECT id FROM users WHERE display_name = ? OR username = ?",
                      (raw, raw))
        if row:
            user_id = row["id"]

    if user_id is None:
        flash(f"没找到「{raw}」这个账号，先在后台建号或检查姓名是否完全一致", "error")
    elif authm.membership(user_id, board_id):
        flash("这位老师已经在组里了", "error")
    else:
        dbm.execute(
            "INSERT INTO board_members (board_id, user_id, role, joined_at) VALUES (?,?,?,?)",
            (board_id, user_id, "member", dbm.now_ts()))
        newbie = dbm.row("SELECT display_name FROM users WHERE id = ?", (user_id,))
        utils.notify([user_id], actor_id=authm.current_user()["id"], kind="board",
                     summary=f"你被加入课题组「{g.board['name']}」",
                     link=url_for("boards.detail", board_id=board_id), board_id=board_id)
        flash(f"已把 {newbie['display_name']} 加入课题组", "ok")
    return redirect(url_for("boards.settings", board_id=board_id))


@bp.route("/b/<int:board_id>/members/<int:user_id>/remove", methods=["POST"])
@authm.board_visible_required
@authm.board_role_required("leader")
def member_remove(board_id, user_id):
    if user_id == g.board["owner_id"]:
        flash("组长不能把自己移出课题组，请先转让组长", "error")
    else:
        dbm.execute("DELETE FROM board_members WHERE board_id = ? AND user_id = ?",
                    (board_id, user_id))
        flash("已移出课题组", "ok")
    return redirect(url_for("boards.settings", board_id=board_id))


@bp.route("/b/<int:board_id>/members/<int:user_id>/role", methods=["POST"])
@authm.board_visible_required
@authm.board_role_required("leader")
def member_role(board_id, user_id):
    role = "leader" if request.form.get("role") == "leader" else "member"
    dbm.execute("UPDATE board_members SET role = ? WHERE board_id = ? AND user_id = ?",
                (role, board_id, user_id))
    flash("已调整组内角色", "ok")
    return redirect(url_for("boards.settings", board_id=board_id))


EXPORT_MODES = {
    "all": "课题全部材料（正文 + 讨论 + 任务 + 附件）",
    "files": "只打包全部附件",
}


@bp.route("/b/<int:board_id>/export")
@authm.board_visible_required
@authm.board_role_required("leader")
def export(board_id):
    """组长专属：把本课题所有材料打成一个 zip 下载。"""
    mode = request.args.get("mode", "all")
    if mode not in EXPORT_MODES:
        mode = "all"
    try:
        path, name = utils.export_board_zip(board_id, mode)
    except Exception:
        from flask import current_app
        current_app.logger.exception("导出课题材料失败")
        flash("打包失败了，请稍后重试或联系管理员", "error")
        return redirect(url_for("boards.detail", board_id=board_id))
    return send_file(str(path), as_attachment=True, download_name=name,
                     mimetype="application/zip")


@bp.route("/b/<int:board_id>/files")
@authm.board_visible_required
def files(board_id):
    """课题组文件库：按时间列出全部附件。"""
    page = max(int(request.args.get("page", 1) or 1), 1)
    per_page = 40
    total = dbm.scalar("SELECT COUNT(*) FROM attachments WHERE board_id = ?", (board_id,), 0)
    items = dbm.rows(
        "SELECT a.*, u.display_name, t.title AS topic_title, t.id AS topic_id"
        " FROM attachments a JOIN users u ON u.id = a.owner_id"
        " LEFT JOIN topics t ON (a.attachable_type = 'topic' AND t.id = a.attachable_id)"
        " WHERE a.board_id = ? ORDER BY a.created_at DESC LIMIT ? OFFSET ?",
        (board_id, per_page, (page - 1) * per_page))
    pages = max((total + per_page - 1) // per_page, 1)
    return render_template("board_files.html", board=g.board, items=items,
                           page=page, pages=pages, total=total,
                           is_leader=authm.is_leader(authm.current_user(), board_id))
