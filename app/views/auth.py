# -*- coding: utf-8 -*-
"""登录 / 退出 / 个人设置。"""
from flask import (Blueprint, flash, redirect, render_template, request, session, url_for)

from .. import auth as authm
from .. import db as dbm

bp = Blueprint("auth", __name__)

DEMO_HINT = "首次使用请用管理员账号 admin 登录，并立刻在「个人设置」里改掉默认密码。"


@bp.route("/login", methods=["GET", "POST"])
def login():
    if authm.current_user() is not None:
        return redirect(url_for("boards.home"))

    next_url = request.args.get("next") or request.form.get("next") or ""
    if not next_url.startswith("/"):
        next_url = ""

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = dbm.row("SELECT * FROM users WHERE username = ?", (username,))
        if user is None or not user["is_active"]:
            flash("账号不存在或已被停用", "error")
        elif not authm.verify_password(password, user["password_hash"]):
            flash("密码不对，再试一次", "error")
        else:
            session.clear()
            session["uid"] = user["id"]
            session.permanent = True
            dbm.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (dbm.now_ts(), user["id"]))
            return redirect(next_url or url_for("boards.home"))

    return render_template("login.html", next_url=next_url, hint=DEMO_HINT)


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    flash("已退出登录", "ok")
    return redirect(url_for("auth.login"))


@bp.route("/me", methods=["GET", "POST"])
@authm.login_required
def me():
    user = authm.current_user()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "profile":
            display_name = (request.form.get("display_name") or "").strip()
            subject = (request.form.get("subject") or "").strip()
            if not display_name:
                flash("姓名不能为空", "error")
            else:
                dbm.execute("UPDATE users SET display_name = ?, subject = ? WHERE id = ?",
                            (display_name[:20], subject[:20], user["id"]))
                flash("资料已更新", "ok")
                return redirect(url_for("auth.me"))

        elif action == "password":
            old = request.form.get("old_password") or ""
            new = request.form.get("new_password") or ""
            new2 = request.form.get("new_password2") or ""
            row = dbm.row("SELECT password_hash FROM users WHERE id = ?", (user["id"],))
            if not authm.verify_password(old, row["password_hash"]):
                flash("原密码不对", "error")
            elif len(new) < 6:
                flash("新密码至少 6 位", "error")
            elif new != new2:
                flash("两次输入的新密码不一致", "error")
            else:
                dbm.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                            (authm.hash_password(new), user["id"]))
                flash("密码已修改", "ok")
                return redirect(url_for("auth.me"))

        return redirect(url_for("auth.me"))

    my_boards = dbm.rows(
        "SELECT b.id, b.name, b.is_public, m.role, m.joined_at FROM board_members m"
        " JOIN boards b ON b.id = m.board_id WHERE m.user_id = ?"
        " ORDER BY CASE m.role WHEN 'leader' THEN 0 ELSE 1 END, b.name",
        (user["id"],))
    stats = dbm.row(
        "SELECT"
        " (SELECT COUNT(*) FROM posts WHERE author_id = ? AND is_deleted = 0) AS posts,"
        " (SELECT COUNT(*) FROM topics WHERE author_id = ?) AS topics,"
        " (SELECT COUNT(*) FROM attachments WHERE owner_id = ?) AS files,"
        " (SELECT COALESCE(SUM(score), 0) FROM task_assignees WHERE user_id = ?) AS score",
        (user["id"],) * 4)
    return render_template("me.html", my_boards=my_boards, stats=stats)
