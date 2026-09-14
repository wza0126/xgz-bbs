# -*- coding: utf-8 -*-
"""登录态、密码哈希、权限判断与装饰器。权限判断只在这一个文件里，视图里不写 if 权限。"""
import base64
import functools
import hashlib
import hmac
import os

from flask import abort, flash, g, redirect, request, session, url_for

from . import db as dbm

_ITERATIONS = 200_000


# ---------- 密码 ----------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(iters)
        )
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


# ---------- 当前用户 ----------

def load_logged_in_user():
    uid = session.get("uid")
    if not uid:
        g.user = None
        return
    g.user = dbm.row(
        "SELECT id, username, display_name, subject, role, is_active"
        " FROM users WHERE id = ? AND is_active = 1",
        (uid,),
    )
    if g.user is None:
        session.pop("uid", None)


def current_user():
    return getattr(g, "user", None)


def is_admin(user=None) -> bool:
    user = user or current_user()
    return bool(user) and user["role"] == "admin"


# ---------- 课题组权限 ----------

def membership(user_id, board_id):
    return dbm.row(
        "SELECT role FROM board_members WHERE board_id = ? AND user_id = ?",
        (board_id, user_id),
    )


def is_member(user, board_id) -> bool:
    if not user:
        return False
    if is_admin(user):
        return True
    return membership(user["id"], board_id) is not None


def is_leader(user, board_id) -> bool:
    if not user:
        return False
    if is_admin(user):
        return True
    m = membership(user["id"], board_id)
    return bool(m) and m["role"] == "leader"


def board_of_topic(topic_id):
    return dbm.row(
        "SELECT b.* FROM boards b JOIN topics t ON t.board_id = b.id WHERE t.id = ?",
        (topic_id,),
    )


def can_view_board(board, user=None) -> bool:
    user = user or current_user()
    if not board:
        return False
    if board["is_public"]:
        return True
    return is_member(user, board["id"])


def board_of_attachment(att):
    """附件所属课题组，用于下载前的可见性校验。"""
    return dbm.row("SELECT * FROM boards WHERE id = ?", (att["board_id"],))


# ---------- 装饰器 ----------

def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login", next=request.full_path))
        if not is_admin():
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _resolve_board_id(kwargs):
    if "board_id" in kwargs:
        return kwargs["board_id"]
    if "topic_id" in kwargs:
        b = board_of_topic(kwargs["topic_id"])
        return b["id"] if b else None
    if "attach_id" in kwargs:
        att = dbm.row("SELECT board_id FROM attachments WHERE id = ?", (kwargs["attach_id"],))
        return att["board_id"] if att else None
    return None


def board_role_required(role="leader"):
    """role='leader' 要求组长或管理员；role='member' 要求组员或管理员。"""
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                return redirect(url_for("auth.login", next=request.full_path))
            board_id = _resolve_board_id(kwargs)
            if board_id is None:
                abort(404)
            ok = is_leader(user, board_id) if role == "leader" else is_member(user, board_id)
            if not ok:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def board_visible_required(view):
    """要求当前用户对该课题组可见（公开组任何人，私有组仅成员）。"""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("auth.login", next=request.full_path))
        board_id = _resolve_board_id(kwargs)
        if board_id is None:
            abort(404)
        board = dbm.row("SELECT * FROM boards WHERE id = ?", (board_id,))
        if board is None:
            abort(404)
        if not can_view_board(board, user):
            abort(403)
        g.board = board
        return view(*args, **kwargs)
    return wrapped
