# -*- coding: utf-8 -*-
"""SQLite 连接与最简数据访问封装（不上 ORM，直接写 SQL）。"""
import sqlite3
import time
from pathlib import Path

from flask import current_app, g


def now_ts() -> int:
    """统一用 UTC 整数时间戳入库。"""
    return int(time.time())


def connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DB_PATH"])
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ---------- 查询糖 ----------

def rows(sql: str, args=()) -> list:
    return get_db().execute(sql, args).fetchall()


def row(sql: str, args=()):
    return get_db().execute(sql, args).fetchone()


def scalar(sql: str, args=(), default=None):
    r = row(sql, args)
    if r is None:
        return default
    v = r[0]
    return default if v is None else v


def execute(sql: str, args=()) -> int:
    """执行写操作并提交，返回 lastrowid。"""
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid


def execute_many(sql: str, seq):
    db = get_db()
    cur = db.executemany(sql, seq)
    db.commit()
    return cur.rowcount


# ---------- 轻量迁移：只做「加列」，不动老数据 ----------
# schema.sql 里的 CREATE TABLE IF NOT EXISTS 只能在建新库时生效，
# 而 SQLite 又没有 ADD COLUMN IF NOT EXISTS，所以老库要靠这里补字段。
# 每条都必须有默认值，否则已有行会违反 NOT NULL。重复运行安全。
_ADDED_COLUMNS = [
    # (表, 列, 定义)   —— 富文本正文：老数据一律 'text'，保持原样显示
    ("topics", "body_format", "TEXT NOT NULL DEFAULT 'text'"),
    ("posts", "body_format", "TEXT NOT NULL DEFAULT 'text'"),
]


def _ensure_columns(conn):
    fixed = []
    for table, col, decl in _ADDED_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not cols:
            continue        # 表都没建出来，说明 schema.sql 有问题，交给上面报错
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            fixed.append(f"{table}.{col}")
    if fixed:
        conn.commit()
    return fixed


def init_db(app):
    """建表 + 补字段 + 灌话题类型种子 + 首次运行自动创建管理员。"""
    schema = (Path(app.root_path) / "schema.sql").read_text(encoding="utf-8")
    conn = connect(app.config["DB_PATH"])
    try:
        conn.executescript(schema)
        conn.commit()
        fixed = _ensure_columns(conn)
        if fixed:
            app.logger.info("补上了缺失的字段：%s", "、".join(fixed))
        # 话题类型清单存库（管理员可在后台增删）。已存在的 code 不会被覆盖，
        # 所以每次启动跑一遍是安全的：只补「种子里有、库里没有」的类型。
        from . import kinds as kindsm
        added = kindsm.seed_defaults(conn)
        if added:
            app.logger.info("话题类型种子已补齐 %d 个", added)
        n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if n == 0:
            from .auth import hash_password
            conn.execute(
                "INSERT INTO users (username, password_hash, display_name, subject, role, created_at)"
                " VALUES (?,?,?,?,?,?)",
                ("admin", hash_password("admin123"), "系统管理员", "信息中心", "admin", now_ts()),
            )
            conn.commit()
            app.logger.warning("已创建初始管理员账号 admin / admin123 —— 请登录后立即修改密码！")
    finally:
        conn.close()
