# -*- coding: utf-8 -*-
"""站内搜索：话题标题/正文、回复内容、附件文件名。"""
from flask import Blueprint, render_template, request

from .. import auth as authm
from .. import db as dbm

bp = Blueprint("search", __name__)


def visible_board_clause(user):
    if authm.is_admin(user):
        return "1 = 1", []
    return ("(b.is_public = 1 OR b.id IN (SELECT board_id FROM board_members WHERE user_id = ?))",
            [user["id"]])


@bp.route("/search")
@authm.login_required
def index():
    user = authm.current_user()
    kw = (request.args.get("q") or "").strip()
    scope = request.args.get("scope", "topics")
    if scope not in ("topics", "posts", "files"):
        scope = "topics"

    clause, clause_args = visible_board_clause(user)
    like = f"%{kw}%"
    topics = posts = files = []
    total = 0

    if kw:
        if scope == "topics":
            topics = dbm.rows(
                "SELECT t.id, t.title, t.body, t.kind, t.created_at, t.reply_count,"
                " b.name AS board_name, b.id AS board_id, u.display_name AS author_name"
                " FROM topics t JOIN boards b ON b.id = t.board_id JOIN users u ON u.id = t.author_id"
                f" WHERE b.is_archived = 0 AND {clause} AND (t.title LIKE ? OR t.body LIKE ?)"
                " ORDER BY t.created_at DESC LIMIT 60", tuple(clause_args + [like, like]))
            total = len(topics)
        elif scope == "posts":
            posts = dbm.rows(
                "SELECT p.id, p.body, p.created_at, p.floor_no, t.id AS topic_id, t.title AS topic_title,"
                " b.name AS board_name, u.display_name AS author_name"
                " FROM posts p JOIN topics t ON t.id = p.topic_id JOIN boards b ON b.id = t.board_id"
                " JOIN users u ON u.id = p.author_id"
                f" WHERE b.is_archived = 0 AND p.is_deleted = 0 AND {clause} AND p.body LIKE ?"
                " ORDER BY p.created_at DESC LIMIT 60", tuple(clause_args + [like]))
            total = len(posts)
        else:
            files = dbm.rows(
                "SELECT a.id, a.orig_name, a.ext, a.size_bytes, a.created_at, a.board_id,"
                " b.name AS board_name, u.display_name, t.id AS topic_id, t.title AS topic_title"
                " FROM attachments a JOIN boards b ON b.id = a.board_id"
                " JOIN users u ON u.id = a.owner_id"
                " LEFT JOIN topics t ON (a.attachable_type = 'topic' AND t.id = a.attachable_id)"
                f" WHERE b.is_archived = 0 AND {clause} AND a.orig_name LIKE ?"
                " ORDER BY a.created_at DESC LIMIT 60", tuple(clause_args + [like]))
            total = len(files)

    return render_template("search.html", q=kw, scope=scope, topics=topics,
                           posts=posts, files=files, total=total)
