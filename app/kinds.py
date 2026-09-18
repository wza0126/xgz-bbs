# -*- coding: utf-8 -*-
"""话题类型（kind）：清单存库，管理员在后台自助增删。

为什么从 config.py 挪到数据库：以前每加一个标签都要改代码 + 加一条 CSS + 重新部署。
现在清单存在 `topic_kinds` 表，代码这边只负责三件事：

  1. 首次建库时把 config 里的种子灌进表（`seed_defaults`）
  2. 每次请求把当前清单读出来给视图和模板用（`all_kinds` / `order` / `labels` …）
  3. 内置语义类型（讨论 / 公告 / 任务）加锁，防止误删误停 —— `can_delete` / `can_disable`

颜色只存「调色板键」（brand / ok / …），CSS 里对应 .t-c-<键>，
所以后台新增一个类型完全不用动 CSS。

`topics.kind` 存的永远是 code。code 创建后不可改 —— 一改历史话题的标签就对不上了。
"""
import time

from flask import g

import config as cfg

from . import db as dbm

COLS = ("id", "code", "label", "color", "sort_order",
        "leader_only", "pinnable", "is_builtin", "is_active")


# ---------------------------------------------------------------- 建库 / 种子

def seed_defaults(conn):
    """把 config 里的种子类型补进表。

    注意：**已存在的 code 一律不动** —— 管理员改过的名字、颜色、顺序不能被覆盖。
    所以这个函数反复调用是安全的（每次升级重启都会跑一遍）。
    """
    have = {r[0] for r in conn.execute("SELECT code FROM topic_kinds")}
    now = int(time.time())
    added = 0
    for i, k in enumerate(cfg.DEFAULT_TOPIC_KINDS, start=1):
        if k["code"] in have:
            continue
        conn.execute(
            "INSERT INTO topic_kinds (code, label, color, sort_order, leader_only,"
            " pinnable, is_builtin, is_active, created_at) VALUES (?,?,?,?,?,?,?,1,?)",
            (k["code"], k["label"], k["color"], i,
             k["leader_only"], k["pinnable"],
             1 if k["code"] in cfg.BUILTIN_KIND_CODES else 0, now))
        added += 1
    conn.commit()
    return added


# ---------------------------------------------------------------- 读取

def _fallback():
    """兜底清单：表被清空（或读不到）时用，保证站点还能正常发帖。"""
    out = []
    for i, k in enumerate(cfg.DEFAULT_TOPIC_KINDS, start=1):
        out.append({
            "id": 0, "code": k["code"], "label": k["label"], "color": k["color"],
            "sort_order": i, "leader_only": k["leader_only"], "pinnable": k["pinnable"],
            "is_builtin": 1 if k["code"] in cfg.BUILTIN_KIND_CODES else 0,
            "is_active": 1,
        })
    return out


def all_kinds():
    """当前全部类型（含已停用的），按显示顺序。结果按请求缓存，一次渲染只查一次库。"""
    cache = getattr(g, "_topic_kinds", None)
    if cache is None:
        try:
            cache = [dict(r) for r in dbm.rows(
                f"SELECT {', '.join(COLS)} FROM topic_kinds ORDER BY sort_order, id")]
        except Exception:  # 表还没建好 / 连接异常 → 退回 config 种子
            cache = []
        if not cache:
            cache = _fallback()
        g._topic_kinds = cache
    return cache


def invalidate():
    """增删改之后清掉请求内缓存，同一请求里继续读要看到新值。"""
    g.pop("_topic_kinds", None)


def labels():
    """code -> 中文名。含停用类型（历史话题的标签还要显示）。"""
    return {k["code"]: k["label"] for k in all_kinds()}


def color_map():
    """code -> 调色板键。含停用类型。"""
    return {k["code"]: k["color"] for k in all_kinds()}


def order():
    """标签栏 / 发布页顺序 —— 只含启用中的类型。"""
    return [k["code"] for k in all_kinds() if k["is_active"]]


def codes():
    """筛选白名单：含停用类型，这样旧链接、历史筛选照样能打开。"""
    return {k["code"] for k in all_kinds()}


def postable():
    """发布白名单：只含启用中的类型。"""
    return {k["code"] for k in all_kinds() if k["is_active"]}


def leader_only():
    return tuple(k["code"] for k in all_kinds() if k["leader_only"])


def pinnable():
    return tuple(k["code"] for k in all_kinds() if k["pinnable"])


def free_labels():
    """组员能发的类型名（按标签栏顺序）—— 发布页提示语直接用。"""
    return [k["label"] for k in all_kinds()
            if k["is_active"] and not k["leader_only"]]


def get(code):
    for k in all_kinds():
        if k["code"] == code:
            return k
    return None


def label_of(code, default=None):
    k = get(code)
    if k is None:
        return default if default is not None else code
    return k["label"]


# ---------------------------------------------------------------- 规则

def is_builtin(code):
    return code in cfg.BUILTIN_KIND_CODES


def can_delete(code):
    """内置类型不许删：代码里有专门流程（任务卡、公告通知、兜底值）。"""
    return not is_builtin(code)


def can_disable(code):
    """兜底类型不许停用，否则发布页就没有默认类型了。"""
    return code not in cfg.UNDISABLABLE_KIND_CODES


def usage(code):
    """这个类型下面有多少个话题（决定能不能删）。"""
    return dbm.scalar("SELECT COUNT(*) FROM topics WHERE kind = ?", (code,), 0)


def next_code():
    """自动生成一个没被占用的标识，例如 k1、k2……管理员不想操心标识时用。"""
    used = {k["code"] for k in all_kinds()}
    n = 1
    while f"k{n}" in used:
        n += 1
    return f"k{n}"


def resequence(rows):
    """按给定顺序重排 sort_order。

    不依赖「相邻两个 sort_order 是否相等」这类假设：上移/下移时先把顺序换好，
    再整体重写一遍序号，怎么都不会乱。
    """
    db = dbm.get_db()
    for i, r in enumerate(rows, start=1):
        db.execute("UPDATE topic_kinds SET sort_order = ? WHERE id = ?", (i, r["id"]))
    db.commit()
