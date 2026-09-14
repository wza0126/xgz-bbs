# -*- coding: utf-8 -*-
"""通知中心。"""
from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from .. import auth as authm
from .. import db as dbm
from .. import utils

bp = Blueprint("notify", __name__)

PAGE_SIZE = 30


@bp.route("/notifications")
@authm.login_required
def center():
    user = authm.current_user()
    scope = request.args.get("scope", "all")
    page = max(int(request.args.get("page", 1) or 1), 1)
    where = ["n.user_id = ?"]
    if scope == "unread":
        where.append("n.is_read = 0")

    total = dbm.scalar(f"SELECT COUNT(*) FROM notifications n WHERE {' AND '.join(where)}",
                       (user["id"],), 0)
    items = dbm.rows(
        "SELECT n.*, u.display_name AS actor_name FROM notifications n"
        " LEFT JOIN users u ON u.id = n.actor_id"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY n.created_at DESC LIMIT ? OFFSET ?",
        (user["id"], PAGE_SIZE, (page - 1) * PAGE_SIZE))
    pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    return render_template("notifications.html", items=items, scope=scope,
                           page=page, pages=pages, total=total)


@bp.route("/notifications/read", methods=["POST"])
@authm.login_required
def read():
    user = authm.current_user()
    nid = request.form.get("id")
    if (nid or "").isdigit():
        dbm.execute("UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
                    (int(nid), user["id"]))
    else:
        dbm.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0",
                    (user["id"],))
    nxt = request.form.get("next")
    if nxt and nxt.startswith("/"):
        return redirect(nxt)
    return redirect(url_for("notify.center"))


@bp.route("/notifications/open/<int:nid>")
@authm.login_required
def open_item(nid):
    """点开一条通知：标记已读并跳到目标。"""
    user = authm.current_user()
    n = dbm.row("SELECT * FROM notifications WHERE id = ? AND user_id = ?", (nid, user["id"]))
    if n is None:
        return redirect(url_for("notify.center"))
    dbm.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (nid,))
    return redirect(n["link"] or url_for("notify.center"))


@bp.route("/notifications/count")
@authm.login_required
def count():
    return jsonify(unread=utils.unread_count(authm.current_user()["id"]))
