# -*- coding: utf-8 -*-
"""应用工厂。"""
import secrets

from flask import (Flask, abort, g, redirect, render_template, request, session,
                   url_for)
from markupsafe import Markup

import config as cfg
from . import auth as authm
from . import db as dbm
from . import utils


def create_app():
    app = Flask(__name__)

    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cfg.BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    app.config.update(
        SECRET_KEY=cfg.load_secret_key(),
        DB_PATH=str(cfg.DB_PATH),
        UPLOAD_DIR=str(cfg.UPLOAD_DIR),
        BACKUP_DIR=str(cfg.BACKUP_DIR),
        MAX_CONTENT_LENGTH=cfg.MAX_CONTENT_LENGTH,
        MAX_UPLOAD_MB=cfg.MAX_UPLOAD_MB,
        BANNED_EXT=cfg.BANNED_EXT,
        INLINE_EXT=cfg.INLINE_EXT,
        PER_PAGE=cfg.PER_PAGE,
        SITE_NAME=cfg.SITE_NAME,
        SITE_SUBTITLE=cfg.SITE_SUBTITLE,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
        JSON_AS_ASCII=False,
        TEMPLATES_AUTO_RELOAD=True,
    )

    dbm.init_db(app)
    app.teardown_appcontext(dbm.close_db)

    # ---------- 蓝图 ----------
    from .views import admin, auth, boards, files, notify, search, tasks, topics
    app.register_blueprint(auth.bp)
    app.register_blueprint(boards.bp)
    app.register_blueprint(topics.bp)
    app.register_blueprint(tasks.bp)
    app.register_blueprint(files.bp)
    app.register_blueprint(notify.bp)
    app.register_blueprint(search.bp)
    app.register_blueprint(admin.bp)

    # ---------- 每个请求加载当前用户 ----------
    @app.before_request
    def _load_user():
        authm.load_logged_in_user()

    # ---------- CSRF ----------
    def _csrf_token():
        tok = session.get("_csrf")
        if not tok:
            tok = secrets.token_urlsafe(32)
            session["_csrf"] = tok
        return tok

    @app.before_request
    def _csrf_protect():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            tok = session.get("_csrf")
            sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
            if not tok or not sent or not secrets.compare_digest(str(tok), str(sent)):
                abort(400, description="页面停留太久或表单已失效，请刷新后重试")

    app.jinja_env.globals["csrf_token"] = _csrf_token

    # ---------- 模板可用 ----------
    app.jinja_env.globals.update(
        SITE_NAME=cfg.SITE_NAME,
        SITE_SUBTITLE=cfg.SITE_SUBTITLE,
        TASK_STATUS=cfg.TASK_STATUS,
        TOPIC_KIND=cfg.TOPIC_KIND,
        MAX_UPLOAD_MB=cfg.MAX_UPLOAD_MB,
        fmt_dt=utils.fmt_dt,
        fmt_day=utils.fmt_day,
        rel_time=utils.rel_time,
        human_size=utils.human_size,
        avatar_style=utils.avatar_style,
        avatar_char=utils.avatar_char,
        file_badge=utils.file_badge,
        due_state=utils.due_state,
        local_today=utils.local_today,
    )
    app.jinja_env.filters["dt"] = utils.fmt_dt
    app.jinja_env.filters["day"] = utils.fmt_day
    app.jinja_env.filters["rel"] = utils.rel_time
    app.jinja_env.filters["size"] = utils.human_size

    @app.context_processor
    def _inject():
        user = authm.current_user()
        return {
            "current_user": user,
            "unread": utils.unread_count(user["id"]) if user else 0,
            "is_admin": authm.is_admin(user),
        }

    # ---------- 首页 ----------
    @app.route("/")
    def index():
        if authm.current_user() is None:
            return redirect(url_for("auth.login", next=request.path))
        return redirect(url_for("boards.home"))

    @app.route("/healthz")
    def healthz():
        return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}

    # ---------- 错误页 ----------
    def _err(code, title, detail):
        return render_template("error.html", code=code, title=title, detail=detail), code

    @app.errorhandler(403)
    def _403(e):
        return _err(403, "没有权限", "这个页面只对课题组内部成员开放。如果你应该能看到它，请联系组长把你加进课题组。")

    @app.errorhandler(404)
    def _404(e):
        return _err(404, "找不到页面", "链接可能已经失效，或者内容被删除了。")

    @app.errorhandler(400)
    def _400(e):
        detail = getattr(e, "description", "") or "请求不合法。"
        return _err(400, "请求无效", detail)

    @app.errorhandler(413)
    def _413(e):
        return _err(413, "文件太大了",
                    f"单个附件不能超过 {cfg.MAX_UPLOAD_MB} MB。压缩一下，或者分几次传。")

    @app.errorhandler(500)
    def _500(e):
        app.logger.exception("内部错误")
        return _err(500, "服务器出错了", "已经记录到日志。刷新一下试试，还不行就把这个时间点告诉管理员。")

    return app
