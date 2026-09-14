# -*- coding: utf-8 -*-
"""管理后台：账号、批量导入、课题组总览。"""
import re

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .. import auth as authm
from .. import db as dbm
from .. import models

bp = Blueprint("admin", __name__, url_prefix="/admin")

DEFAULT_PASSWORD = "123456"

# 删除账号前要盘点的「内容足迹」。有任意一条，就不许硬删——
# 那些内容都挂在课题组的历史记录上，删人会让记录变成孤儿。
FOOTPRINT = (
    ("话题", "SELECT COUNT(*) FROM topics WHERE author_id = ?"),
    ("回复", "SELECT COUNT(*) FROM posts WHERE author_id = ?"),
    ("任务", "SELECT COUNT(*) FROM tasks WHERE created_by = ?"),
    ("附件", "SELECT COUNT(*) FROM attachments WHERE owner_id = ?"),
    ("审核记录", "SELECT COUNT(*) FROM task_assignees WHERE reviewed_by = ?"),
)


def _back(user_id):
    """回到账号列表，并把刚才操作的那一行自动展开（不然面板一刷新就收起，看着像没生效）。"""
    return redirect(url_for("admin.users", open=user_id) + f"#u{user_id}")


def _usable_admins():
    return dbm.scalar("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1", (), 0)


@bp.route("/")
@authm.admin_required
def home():
    stats = dbm.row(
        "SELECT"
        " (SELECT COUNT(*) FROM users WHERE is_active = 1) AS users,"
        " (SELECT COUNT(*) FROM boards WHERE is_archived = 0) AS boards,"
        " (SELECT COUNT(*) FROM topics) AS topics,"
        " (SELECT COUNT(*) FROM posts WHERE is_deleted = 0) AS posts,"
        " (SELECT COUNT(*) FROM tasks) AS tasks,"
        " (SELECT COUNT(*) FROM attachments) AS files,"
        " (SELECT COALESCE(SUM(size_bytes), 0) FROM attachments) AS bytes")
    biggest = dbm.rows(
        "SELECT b.id, b.name, COUNT(a.id) AS n, COALESCE(SUM(a.size_bytes), 0) AS bytes"
        " FROM boards b LEFT JOIN attachments a ON a.board_id = b.id"
        " GROUP BY b.id ORDER BY bytes DESC LIMIT 8")
    last_logins = dbm.rows(
        "SELECT username, display_name, subject, last_login_at FROM users WHERE is_active = 1"
        " ORDER BY (last_login_at IS NULL), last_login_at DESC LIMIT 10")
    return render_template("admin_home.html", stats=stats, biggest=biggest, last_logins=last_logins)


@bp.route("/users")
@authm.admin_required
def users():
    kw = (request.args.get("q") or "").strip()
    like = f"%{kw}%"
    if kw:
        rows = dbm.rows(
            "SELECT u.*, (SELECT COUNT(*) FROM board_members m WHERE m.user_id = u.id) AS board_count"
            " FROM users u WHERE u.username LIKE ? OR u.display_name LIKE ? OR u.subject LIKE ?"
            " ORDER BY u.role, u.display_name", (like, like, like))
    else:
        rows = dbm.rows(
            "SELECT u.*, (SELECT COUNT(*) FROM board_members m WHERE m.user_id = u.id) AS board_count"
            " FROM users u ORDER BY u.role, u.display_name")
    return render_template("admin_users.html", rows=rows, q=kw,
                           open_id=request.args.get("open", type=int))


@bp.route("/users/new", methods=["POST"])
@authm.admin_required
def user_new():
    username = (request.form.get("username") or "").strip()
    display_name = (request.form.get("display_name") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    password = (request.form.get("password") or "").strip() or DEFAULT_PASSWORD
    role = "admin" if request.form.get("role") == "admin" else "teacher"

    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        flash("账号只能是 3-32 位字母、数字、下划线、点或横线", "error")
    elif not display_name:
        flash("请填写姓名", "error")
    elif len(password) < 6:
        flash("密码至少 6 位", "error")
    elif dbm.row("SELECT id FROM users WHERE username = ?", (username,)):
        flash(f"账号 {username} 已存在", "error")
    else:
        dbm.execute(
            "INSERT INTO users (username, password_hash, display_name, subject, role, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (username, authm.hash_password(password), display_name[:20], subject[:20],
             role, dbm.now_ts()))
        flash(f"已创建账号 {username}（{display_name}），初始密码 {password}", "ok")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/reset", methods=["POST"])
@authm.admin_required
def user_reset(user_id):
    u = dbm.row("SELECT * FROM users WHERE id = ?", (user_id,))
    if u is None:
        flash("账号不存在", "error")
        return redirect(url_for("admin.users"))
    new_pw = (request.form.get("password") or "").strip() or DEFAULT_PASSWORD
    if len(new_pw) < 6:
        flash("密码至少 6 位", "error")
    else:
        dbm.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                    (authm.hash_password(new_pw), user_id))
        flash(f"「{u['display_name']}」的密码已重置为 {new_pw}，请转告本人尽快改掉。", "ok")
    return _back(user_id)


@bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@authm.admin_required
def user_toggle(user_id):
    if user_id == authm.current_user()["id"]:
        flash("不能停用自己正在用的账号。", "error")
        return _back(user_id)
    u = dbm.row("SELECT * FROM users WHERE id = ?", (user_id,))
    if u is None:
        flash("账号不存在", "error")
    else:
        if u["is_active"] and u["role"] == "admin" and _usable_admins() <= 1:
            flash("这是唯一一个还能用的管理员，停用后就没人能进后台了。", "error")
            return _back(user_id)
        dbm.execute("UPDATE users SET is_active = ? WHERE id = ?",
                    (0 if u["is_active"] else 1, user_id))
        flash(f"{u['display_name']} 已{'停用（登录不了，但历史内容都保留）' if u['is_active'] else '启用'}", "ok")
    return _back(user_id)


@bp.route("/users/<int:user_id>/update", methods=["POST"])
@authm.admin_required
def user_update(user_id):
    u = dbm.row("SELECT * FROM users WHERE id = ?", (user_id,))
    if u is None:
        flash("账号不存在", "error")
        return redirect(url_for("admin.users"))

    display_name = (request.form.get("display_name") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    role = "admin" if request.form.get("role") == "admin" else "teacher"
    username = (request.form.get("username") or "").strip() or u["username"]
    me = authm.current_user()

    if u["role"] == "admin" and role != "admin" and _usable_admins() <= 1:
        flash("这是唯一一个还能用的管理员，不能降成普通教师——会没人能进后台。", "error")
    elif user_id == me["id"] and role != "admin":
        flash("不能把自己降成普通教师。", "error")
    elif not display_name:
        flash("姓名不能为空", "error")
    elif not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        flash("登录账号只能是 3-32 位字母、数字、下划线、点或横线。", "error")
    elif dbm.row("SELECT id FROM users WHERE username = ? AND id != ?", (username, user_id)):
        flash(f"登录账号 {username} 已经被别人占用了，换一个。", "error")
    else:
        dbm.execute(
            "UPDATE users SET username = ?, display_name = ?, subject = ?, role = ? WHERE id = ?",
            (username, display_name[:20], subject[:20], role, user_id))
        bits = [f"{display_name} · {subject or '未填学科'} · {'管理员' if role == 'admin' else '教师'}"]
        if username != u["username"]:
            bits.append(f"登录账号 {u['username']} → {username}")
        flash("已保存：" + "，".join(bits), "ok")
    return _back(user_id)


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@authm.admin_required
def user_delete(user_id):
    me = authm.current_user()
    u = dbm.row("SELECT * FROM users WHERE id = ?", (user_id,))
    if u is None:
        flash("账号不存在", "error")
        return redirect(url_for("admin.users"))
    if user_id == me["id"]:
        flash("不能删除自己正在用的账号。", "error")
        return _back(user_id)

    owned = dbm.rows("SELECT name FROM boards WHERE owner_id = ? ORDER BY id", (user_id,))
    if owned:
        names = "、".join(r["name"] for r in owned[:3]) + ("…" if len(owned) > 3 else "")
        flash(f"「{u['display_name']}」还是课题组「{names}」的组长。"
              f"先去「课题组总览」把组长转给别人，再回来删。", "error")
        return _back(user_id)

    left = [(label, dbm.scalar(sql, (user_id,), 0)) for label, sql in FOOTPRINT]
    left = [(label, n) for label, n in left if n]
    if left:
        detail = "、".join(f"{label} {n} 条" for label, n in left)
        flash(f"「{u['display_name']}」名下还有 {detail}，直接删会让课题组的历史记录断掉，所以没删。"
              f"改用「停用」吧——停用后 TA 登不进来，但发过的话题和交过的材料都还在。", "error")
        return _back(user_id)

    if u["role"] == "admin" and _usable_admins() <= 1:
        flash("这是唯一一个还能用的管理员，不能删除。", "error")
        return _back(user_id)

    # 到这里说明该账号没有任何内容足迹，可以安全删除。
    # 关联表手工清一遍（不依赖 FK 级联开关），通知里它作为「触发者」的字段置空，别牵连别人的通知。
    db = dbm.get_db()
    db.execute("DELETE FROM board_members  WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM task_assignees WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM notifications  WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM reactions      WHERE user_id = ?", (user_id,))
    db.execute("UPDATE notifications SET actor_id = NULL WHERE actor_id = ?", (user_id,))
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    flash(f"账号「{u['display_name']}（{u['username']}）」已删除。", "ok")
    return redirect(url_for("admin.users"))


@bp.route("/import", methods=["GET", "POST"])
@authm.admin_required
def import_users():
    report = None
    if request.method == "POST":
        text = request.form.get("text") or ""
        default_pw = (request.form.get("default_password") or "").strip() or DEFAULT_PASSWORD
        created, skipped = [], []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in re.split(r"[,\t\uFF0C|]+", line) if p.strip()]
            if len(parts) < 2:
                skipped.append((line, "至少要有「姓名,账号」两列"))
                continue
            display_name, username = parts[0], parts[1]
            password = parts[2] if len(parts) > 2 and parts[2] else default_pw
            subject = parts[3] if len(parts) > 3 else ""
            if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
                skipped.append((line, "账号格式不对（3-32 位字母数字_.-）"))
                continue
            if len(password) < 6:
                skipped.append((line, "密码少于 6 位"))
                continue
            if dbm.row("SELECT id FROM users WHERE username = ?", (username,)):
                skipped.append((line, "账号已存在"))
                continue
            dbm.execute(
                "INSERT INTO users (username, password_hash, display_name, subject, role, created_at)"
                " VALUES (?,?,?,?,'teacher',?)",
                (username, authm.hash_password(password), display_name[:20], subject[:20],
                 dbm.now_ts()))
            created.append((display_name, username, password))
        report = {"created": created, "skipped": skipped}
        flash(f"导入完成：新增 {len(created)} 个，跳过 {len(skipped)} 行", "ok")
    return render_template("admin_import.html", report=report, default_password=DEFAULT_PASSWORD)


@bp.route("/boards")
@authm.admin_required
def boards():
    rows = dbm.rows(
        "SELECT b.*, u.display_name AS owner_name,"
        " (SELECT COUNT(*) FROM board_members m WHERE m.board_id = b.id) AS member_count,"
        " (SELECT COUNT(*) FROM topics t WHERE t.board_id = b.id) AS topic_count,"
        " (SELECT COUNT(*) FROM tasks k WHERE k.board_id = b.id) AS task_count,"
        " (SELECT COUNT(*) FROM attachments a WHERE a.board_id = b.id) AS file_count"
        " FROM boards b JOIN users u ON u.id = b.owner_id ORDER BY b.is_archived, b.id DESC")
    return render_template("admin_boards.html", rows=rows,
                           users=dbm.rows("SELECT id, username, display_name FROM users"
                                          " WHERE is_active = 1 ORDER BY display_name"))


@bp.route("/boards/<int:board_id>/archive", methods=["POST"])
@authm.admin_required
def board_archive(board_id):
    b = dbm.row("SELECT * FROM boards WHERE id = ?", (board_id,))
    if b is None:
        flash("课题组不存在", "error")
    else:
        dbm.execute("UPDATE boards SET is_archived = ? WHERE id = ?",
                    (0 if b["is_archived"] else 1, board_id))
        flash(f"「{b['name']}」已{'恢复' if b['is_archived'] else '归档'}", "ok")
    return redirect(url_for("admin.boards"))


@bp.route("/boards/<int:board_id>/owner", methods=["POST"])
@authm.admin_required
def board_owner(board_id):
    uid = request.form.get("user_id")
    if not (uid or "").isdigit():
        flash("请选择新的组长", "error")
        return redirect(url_for("admin.boards"))
    uid = int(uid)
    u = dbm.row("SELECT display_name FROM users WHERE id = ? AND is_active = 1", (uid,))
    if u is None:
        flash("账号不存在", "error")
        return redirect(url_for("admin.boards"))
    dbm.execute("UPDATE boards SET owner_id = ? WHERE id = ?", (uid, board_id))
    dbm.execute("INSERT OR REPLACE INTO board_members (board_id, user_id, role, joined_at)"
                " VALUES (?,?, 'leader', COALESCE((SELECT joined_at FROM board_members"
                " WHERE board_id = ? AND user_id = ?), ?))",
                (board_id, uid, board_id, uid, dbm.now_ts()))
    flash(f"已把组长转给 {u['display_name']}", "ok")
    return redirect(url_for("admin.boards"))
