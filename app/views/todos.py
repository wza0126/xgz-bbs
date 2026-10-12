# -*- coding: utf-8 -*-
"""行动研究待办事项：平台级的一张清单。

- **谁能看**：登录后所有人（入口置顶在帖子列表最上方，随时点开看进展）
- **谁能改**：只有管理员（增 / 改 / 删 / 勾选 / 拖动排序）
- 排序：未完成项按人工优先级 sort_order 排，越小越靠前（越重要）；
  勾选完成后自动沉到列表末尾，取消勾选即回到未完成组。
- 编号：按「当前显示顺序」现算（模板里 loop.index），所以拖动后自动重排，
  库里不另存编号列，省得两边对不上。
"""
import calendar
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import auth as authm
from .. import db as dbm
from .. import models

bp = Blueprint("todos", __name__)

CONTENT_MAX = 500     # 单条内容长度上限
YEAR_SPAN = 5         # 截止日期年份下拉：今年起往后 5 年


def _years(extra=()):
    """年份下拉的候选：今年起往后若干年。编辑已有条目时，把它原有的年份
    也一并放进去（否则那条的年份会被悄悄改成下拉里的第一个）。"""
    y = date.today().year
    return sorted(set(range(y, y + YEAR_SPAN)) | {int(v) for v in extra if str(v).isdigit()})


def _parse_due(form):
    """三个下拉（年/月/日）合成 'YYYY-MM-DD'；没选年份 = 暂不限期，返回 None。"""
    y = (form.get("due_y") or "").strip()
    if not y.isdigit():
        return None
    yi = int(y)
    if not (1970 <= yi <= 2100):
        return None
    m = (form.get("due_m") or "").strip()
    d = (form.get("due_d") or "").strip()
    mi = int(m) if m.isdigit() else 1
    di = int(d) if d.isdigit() else 1
    mi = max(1, min(mi, 12))
    # 日超出该月天数就夹到月末（例：2 月 31 日 → 2 月 28/29 日），不让它烂在库里
    di = max(1, min(di, calendar.monthrange(yi, mi)[1]))
    return "%04d-%02d-%02d" % (yi, mi, di)


def _require(todo_id):
    t = models.todo_get(todo_id)
    if t is None:
        abort(404)
    return t


def _back():
    return redirect(url_for("todos.index"))


# ---------- 查看（所有人）----------

@bp.route("/todos")
@authm.login_required
def index():
    items = models.todo_list()
    open_n = sum(1 for it in items if not it["is_done"])
    extra_years = [str(it["due_date"])[:4] for it in items if it["due_date"]]
    return render_template("todos.html", items=items,
                           open_n=open_n, done_n=len(items) - open_n,
                           years=_years(extra_years))


# ---------- 写操作（仅管理员）----------

@bp.route("/todos/add", methods=["POST"])
@authm.admin_required
def add():
    content = (request.form.get("content") or "").strip()
    if not content:
        flash("待办内容不能为空", "error")
        return _back()
    due = _parse_due(request.form)
    ts = dbm.now_ts()
    # 追加到未完成组的最后一位
    nxt = dbm.scalar("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM action_todos"
                     " WHERE is_done = 0", (), 0)
    dbm.execute(
        "INSERT INTO action_todos (content, due_date, is_done, sort_order,"
        " created_by, created_at, updated_at) VALUES (?,?,0,?,?,?,?)",
        (content[:CONTENT_MAX], due, nxt, authm.current_user()["id"], ts, ts))
    flash("已添加待办事项", "ok")
    return _back()


@bp.route("/todos/<int:todo_id>/edit", methods=["POST"])
@authm.admin_required
def edit(todo_id):
    _require(todo_id)
    content = (request.form.get("content") or "").strip()
    if not content:
        flash("待办内容不能为空", "error")
        return _back()
    dbm.execute("UPDATE action_todos SET content = ?, due_date = ?, updated_at = ?"
                " WHERE id = ?",
                (content[:CONTENT_MAX], _parse_due(request.form), dbm.now_ts(), todo_id))
    flash("待办事项已更新", "ok")
    return _back()


@bp.route("/todos/<int:todo_id>/delete", methods=["POST"])
@authm.admin_required
def delete(todo_id):
    _require(todo_id)
    dbm.execute("DELETE FROM action_todos WHERE id = ?", (todo_id,))
    flash("待办事项已删除", "ok")
    return _back()


@bp.route("/todos/<int:todo_id>/toggle", methods=["POST"])
@authm.admin_required
def toggle(todo_id):
    t = _require(todo_id)
    ts = dbm.now_ts()
    if t["is_done"]:
        # 取消勾选：恢复未完成，回到它原来的人工顺位（sort_order 一直留着）
        dbm.execute("UPDATE action_todos SET is_done = 0, done_at = NULL, updated_at = ?"
                    " WHERE id = ?", (ts, todo_id))
    else:
        # 勾选完成：记录完成时间，列表里会自动沉到末尾
        dbm.execute("UPDATE action_todos SET is_done = 1, done_at = ?, updated_at = ?"
                    " WHERE id = ?", (ts, ts, todo_id))
    return _back()


def _renumber(ids):
    """按给定顺序把未完成项重排 sort_order = 0,1,2…（编号页面现算，这里只存顺序）。"""
    ts = dbm.now_ts()
    for pos, tid in enumerate(ids):
        dbm.execute("UPDATE action_todos SET sort_order = ?, updated_at = ? WHERE id = ?",
                    (pos, ts, tid))


def _open_ids():
    return [r["id"] for r in dbm.rows(
        "SELECT id FROM action_todos WHERE is_done = 0 ORDER BY sort_order, id")]


@bp.route("/todos/reorder", methods=["POST"])
@authm.admin_required
def reorder():
    """拖动排序落库：order[] = 未完成项的新顺序（已完成的不参与，它们在末尾）。

    只认「未完成」的 id，并去重、保序；没被提到的未完成项按原来的先后接在后面，
    最后整组重排成 0,1,2… —— 这样 sort_order 永远连续，不会出现两条并列。
    """
    current = _open_ids()
    valid = set(current)
    seen, ordered = set(), []
    for raw in request.form.getlist("order"):
        if raw.isdigit():
            tid = int(raw)
            if tid in valid and tid not in seen:
                seen.add(tid)
                ordered.append(tid)
    ordered += [tid for tid in current if tid not in seen]
    if ordered:
        _renumber(ordered)
    if request.headers.get("X-Requested-With") == "fetch":
        return "", 204
    return _back()


@bp.route("/todos/<int:todo_id>/move", methods=["POST"])
@authm.admin_required
def move(todo_id):
    """↑ / ↓ 按钮（拖动排序的无 JS 兜底，手机上也好使）。"""
    t = _require(todo_id)
    if t["is_done"]:
        abort(404)
    ids = _open_ids()
    if todo_id not in ids:
        abort(404)
    i = ids.index(todo_id)
    j = i - 1 if request.form.get("dir") == "up" else i + 1
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
        _renumber(ids)
    return _back()
