# -*- coding: utf-8 -*-
"""任务提交 / 删回复 / 楼中楼 的回归测试（对着跑起来的服务打，只读+可控写）。

为什么单独一个脚本：smoke_test.py 管的是「页面都能打开」，
这里管的是**状态机的正确性** —— 这几条曾经真出过问题：

  1. 删除任务提交帖后，侧栏还写着「已提交」、讨论区里却找不到（外键的
     ON DELETE SET NULL 对软删除不生效，状态没跟着退回来）
  2. 重新提交时把新内容写回那条已经废掉的帖子上，于是「交了也看不见」
  3. 父帖被删后，挂在它下面的楼中楼回复跟着一起消失
  4. 删除改成「进回收站」之后，任何一处查询漏了 is_deleted 过滤，
     删掉的内容就会从列表、搜索或下载里冒出来
  5. 行动研究待办：入口要置顶在帖子列表上方、只有管理员能改，勾选完成要
     沉到末尾且能取消还原，拖动重排后编号要跟着显示顺序走

用法（先起服务、准备好演示数据）：
    BBS_DATA_DIR=D:/tmp/x  python tools/seed_demo.py --reset
    python server.py --port 8300 --data-dir D:/tmp/x --host 127.0.0.1
    BBS_TEST_BASE=http://127.0.0.1:8300 BBS_DATA_DIR=D:/tmp/x python tools/check_flows.py

会往库里写东西（任务提交、临时话题、一条附件记录），所以**别对着线上跑**。
"""
import http.cookiejar
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.environ.get("BBS_TEST_BASE", "http://127.0.0.1:8300")
DATA = Path(os.environ.get("BBS_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
DB = DATA / "bbs.db"
ADMIN_PASSWORD = os.environ.get("BBS_ADMIN_PASSWORD", "admin123")
PW = os.environ.get("BBS_DEMO_PASSWORD", "123456")
TAG = "FLOW%d" % os.getpid()

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else "   <<< %s" % (detail,)))


def con():
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row
    return c


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


class Client:
    """带 cookie 的小客户端。GET 跟随重定向，POST 不跟随（好断言 302）。"""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect)
        self.op.addheaders = [("User-Agent", "check-flows")]
        self.tok = None

    def _scan(self, html):
        m = re.search(r'name="_csrf" value="([^"]+)"', html or "")
        if m:
            self.tok = m.group(1)

    def get(self, path, _hops=0):
        url = path if path.startswith("http") else BASE + path
        try:
            r = self.op.open(url, timeout=20)
            code, body, loc = r.status, r.read().decode("utf-8", "replace"), r.headers.get("Location")
        except urllib.error.HTTPError as e:
            code, body, loc = e.code, e.read().decode("utf-8", "replace"), e.headers.get("Location")
        # 登录后首页会 302 到 /plaza，不跟就拿不到新会话的 CSRF 令牌
        if code in (301, 302, 303, 307, 308) and loc and _hops < 3:
            return self.get(loc, _hops + 1)
        self._scan(body)
        return code, body

    def post(self, path, fields):
        if not self.tok:
            self.get("/login")
        data = dict(fields)
        data["_csrf"] = self.tok
        req = urllib.request.Request(
            BASE + path, data=urllib.parse.urlencode(data, doseq=True).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            r = self.op.open(req, timeout=20)
            code, body = r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            code, body = e.code, e.read().decode("utf-8", "replace")
        self._scan(body)
        return code, body

    def login(self, username, password):
        self.get("/login")
        code, _ = self.post("/login", {"username": username, "password": password, "next": ""})
        # 登录里做了 session.clear()，CSRF 令牌必然换新 —— 抓个页面把新令牌取回来
        self.get("/plaza")
        return code == 302


def resolve_fixture(c):
    """演示数据里挑一组：一个还没提交的组员 + 该组组长 + 一个任务话题。"""
    t = c.execute("SELECT id, board_id FROM topics WHERE title LIKE '撰写《项目式学习课例》%'"
                  " ORDER BY id LIMIT 1").fetchone()
    if t is None:
        sys.exit("[夹具] 找不到演示话题，请先跑 tools/seed_demo.py --reset")
    asg = c.execute(
        "SELECT a.id, a.user_id, a.status, u.username, u.display_name FROM task_assignees a"
        " JOIN users u ON u.id = a.user_id JOIN tasks k ON k.id = a.task_id"
        " WHERE k.topic_id = ? AND a.status IN ('todo','doing','rejected')"
        " ORDER BY a.id LIMIT 1", (t["id"],)).fetchone()
    if asg is None:
        sys.exit("[夹具] 该任务下没有「未提交」的组员，请重跑 seed_demo.py --reset")
    leader = c.execute(
        "SELECT u.username FROM board_members m JOIN users u ON u.id = m.user_id"
        " WHERE m.board_id = ? AND m.role = 'leader' LIMIT 1", (t["board_id"],)).fetchone()
    if leader is None:
        sys.exit("[夹具] 该课题组没有组长")
    return t, asg, leader


# ---------------------------------------------------------------- 用例

def case_submit_and_delete(c, t, asg, leader):
    """B：删掉提交帖，任务状态要退回「进行中」并清掉提交/审核痕迹。"""
    mem = Client()
    check("组员登录", mem.login(asg["username"], PW))
    mark1 = "FLOWSUB-A-%s" % TAG
    code, _ = mem.post("/t/%d/submit" % t["id"], {"body": mark1, "body_format": "text"})
    check("提交任务返回 302", code == 302, code)
    row = c.execute("SELECT status, submission_post_id FROM task_assignees WHERE id = ?",
                    (asg["id"],)).fetchone()
    check("提交后状态 = done", row["status"] == "done", row["status"])
    sid = row["submission_post_id"]
    check("提交后拿到 submission_post_id", bool(sid), sid)

    ld = Client()
    check("组长登录", ld.login(leader["username"], PW))
    code, _ = ld.post("/p/%d/delete" % sid, {})
    check("删提交帖返回 302", code == 302, code)
    row = c.execute("SELECT status, submission_post_id, submitted_at, score, review_note,"
                    " reviewed_by, reviewed_at FROM task_assignees WHERE id = ?",
                    (asg["id"],)).fetchone()
    check("B：状态退回 doing", row["status"] == "doing", row["status"])
    check("B：清空 submission_post_id", row["submission_post_id"] is None, row["submission_post_id"])
    check("B：清空 submitted_at", row["submitted_at"] is None, row["submitted_at"])
    check("B：评分/评语/审核痕迹一起清掉",
          row["score"] is None and row["reviewed_by"] is None and row["reviewed_at"] is None
          and (row["review_note"] or "") == "", dict(row))
    p = c.execute("SELECT is_deleted, body, deleted_at, deleted_by FROM posts WHERE id = ?",
                  (sid,)).fetchone()
    check("B：旧提交帖被软删、正文保留（进回收站还能恢复）",
          p["is_deleted"] == 1 and p["body"] == mark1 and p["deleted_at"] is not None, dict(p))
    n = c.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND summary LIKE '%撤回%'",
                  (asg["user_id"],)).fetchone()[0]
    check("B：提交人收到「被撤回」通知", n >= 1, n)
    code, html = ld.get("/t/%d" % t["id"])
    check("B：页面提示了状态回退", "退回「进行中」" in html)
    check("B：讨论区已看不到那条提交", mark1 not in html)
    return mem, sid


def case_resubmit(c, t, asg, mem, sid):
    """C：重新提交必须另起一条新回复，绝不能写回已删除的废帖。"""
    mark2 = "FLOWSUB-B-%s" % TAG
    mem.post("/t/%d/submit" % t["id"], {"body": mark2, "body_format": "text"})
    row = c.execute("SELECT submission_post_id FROM task_assignees WHERE id = ?",
                    (asg["id"],)).fetchone()
    new1 = row["submission_post_id"]
    check("C(a)：重新提交＝新帖（不是旧 id）", bool(new1) and new1 != sid, "new=%s old=%s" % (new1, sid))
    p = c.execute("SELECT is_deleted, body FROM posts WHERE id = ?", (new1,)).fetchone()
    check("C(a)：新帖未删、正文正确", bool(p) and p["is_deleted"] == 0 and mark2 in p["body"],
          dict(p) if p else None)

    # 模拟历史坏数据：指针指回一条已删除的帖子
    c.execute("UPDATE task_assignees SET submission_post_id = ?, status = 'done' WHERE id = ?",
              (sid, asg["id"]))
    c.commit()
    mark3 = "FLOWSUB-C-%s" % TAG
    mem.post("/t/%d/submit" % t["id"], {"body": mark3, "body_format": "text"})
    row = c.execute("SELECT submission_post_id FROM task_assignees WHERE id = ?",
                    (asg["id"],)).fetchone()
    new3 = row["submission_post_id"]
    check("C(b)：指针指向已删帖时另起新帖", new3 not in (None, sid), "new=%s 废帖=%s" % (new3, sid))
    p = c.execute("SELECT is_deleted, body FROM posts WHERE id = ?", (new3,)).fetchone()
    check("C(b)：新帖可见、正文正确", bool(p) and p["is_deleted"] == 0 and mark3 in p["body"],
          dict(p) if p else None)
    old = c.execute("SELECT is_deleted, body FROM posts WHERE id = ?", (sid,)).fetchone()
    check("C(b)：没有把新内容写回废帖", old["is_deleted"] == 1 and mark3 not in (old["body"] or ""),
          dict(old))
    code, html = mem.get("/t/%d" % t["id"])
    check("C(b)：讨论区能看到新提交", mark3 in html)


def case_orphan(c, t, mem):
    """父帖被删后，楼中楼回复要提上来，不能跟着消失。"""
    board = t["board_id"]
    tmark = "FLOWORPH-%s" % TAG
    mem.post("/b/%d/new" % board, {"kind": "discussion", "title": tmark,
                                   "body": "孤儿场景宿主帖", "body_format": "text"})
    row = c.execute("SELECT id FROM topics WHERE title = ?", (tmark,)).fetchone()
    if row is None:
        check("孤儿：新建话题成功", False, "没建出来，后面的用例跳过")
        return
    tid = row["id"]
    mem.post("/t/%d/reply" % tid, {"body": "PARENT-%s" % TAG, "body_format": "text"})
    pid = c.execute("SELECT id FROM posts WHERE topic_id = ? AND body LIKE 'PARENT-%'",
                    (tid,)).fetchone()["id"]
    mem.post("/t/%d/reply" % tid, {"body": "CHILD-%s" % TAG, "body_format": "text",
                                   "parent_id": pid})
    cid = c.execute("SELECT id FROM posts WHERE topic_id = ? AND body LIKE 'CHILD-%'",
                    (tid,)).fetchone()["id"]

    code, html = mem.get("/t/%d" % tid)
    check("孤儿：删前父子都在页面上", ("PARENT-%s" % TAG) in html and ("CHILD-%s" % TAG) in html)
    check("孤儿：删前子回复是嵌套的", 'class="subpost"' in html,
          "subpost=%d" % html.count('class="subpost"'))
    mem.post("/p/%d/delete" % pid, {})
    code, html = mem.get("/t/%d" % tid)
    check("孤儿：父帖已从页面消失", ("PARENT-%s" % TAG) not in html)
    check("孤儿：子回复仍然显示（本次修的就是它）", ("CHILD-%s" % TAG) in html)
    check("孤儿：子回复已提为独立楼层", html.count('class="subpost"') == 0,
          "subpost=%d" % html.count('class="subpost"'))
    check("孤儿：子回复的帖子本体没被误删",
          c.execute("SELECT is_deleted FROM posts WHERE id = ?", (cid,)).fetchone()["is_deleted"] == 0)
    cnt = c.execute("SELECT COUNT(*) FROM posts WHERE topic_id = ? AND is_deleted = 0",
                    (tid,)).fetchone()[0]
    check("孤儿：库里可见回复数 = 1", cnt == 1, cnt)


def _forms(html):
    return re.findall(r"<form\b[^>]*>", html)


def _native_only(html):
    """只剩原生 onsubmit、没接 data-confirm 的表单 —— 那才算漏网。"""
    return [f for f in _forms(html)
            if 'onsubmit="return confirm(' in f and "data-confirm=" not in f]


def case_confirm_wiring(c, t, leader, board):
    """确认弹窗接线：删除类表单要挂 data-confirm，且不能有漏网的纯原生 confirm。

    模板上的 onsubmit 是**故意留的兜底**（没 JS 时还得拦一下），
    所以判据是「有 onsubmit 却没 data-confirm」，而不是「有 onsubmit 就算漏」。
    """
    ld = Client()
    ld.login(leader["username"], PW)
    code, html = ld.get("/t/%d" % t["id"])
    check("话题页渲染正常", code == 200, code)
    check("话题页有确认弹窗容器", 'id="confirmModal"' in html)
    wired = [f for f in _forms(html) if "data-confirm=" in f]
    check("话题页删除表单接了 data-confirm（≥2 处）", len(wired) >= 2, len(wired))
    check("话题页没有漏网的纯原生 confirm 表单", not _native_only(html), _native_only(html)[:1])

    ad = Client()
    check("管理员登录", ad.login("admin", ADMIN_PASSWORD))
    for path, label in (("/admin/users", "后台账号页"), ("/admin/kinds", "后台类型标签页")):
        code, html = ad.get(path)
        check("%s 正常且接了弹窗" % label,
              code == 200 and 'data-confirm=' in html and 'id="confirmModal"' in html,
              "code=%s" % code)
        check("%s 没有漏网的纯原生 confirm 表单" % label, not _native_only(html),
              _native_only(html)[:1])

    code, html = ld.get("/b/%d/settings" % board)
    check("课题组设置页正常且接了弹窗",
          code == 200 and 'data-confirm=' in html and 'id="confirmModal"' in html, "code=%s" % code)


def case_trash(c, t, leader, board, mem):
    """回收站：话题整体进出、回复单独进出、附件跟随宿主、彻底删除、权限。

    这是「删除 = 软删除」这套机制的回归 —— 一旦哪天有人把删改回真删，
    或者某条查询漏了 is_deleted 过滤，这里就会红。
    """
    up = DATA / "uploads" / "2026" / "10"
    up.mkdir(parents=True, exist_ok=True)
    fname = "trash-%s.txt" % TAG
    fpath = up / fname
    fpath.write_text("回收站附件夹具", encoding="utf-8")

    tmark = "TRASHT-%s" % TAG
    rmark = "TRASHREPLY-%s" % TAG
    mem.post("/b/%d/new" % board, {"kind": "discussion", "title": tmark,
                                   "body": "回收站用例宿主", "body_format": "text"})
    row = c.execute("SELECT id FROM topics WHERE title = ?", (tmark,)).fetchone()
    if row is None:
        check("回收站：新建话题成功", False, "没建出来，后面的用例跳过")
        return
    tid = row["id"]
    mem.post("/t/%d/reply" % tid, {"body": rmark, "body_format": "text"})
    pid = c.execute("SELECT id FROM posts WHERE topic_id = ? AND body = ?",
                    (tid, rmark)).fetchone()["id"]
    owner = c.execute("SELECT author_id FROM posts WHERE id = ?", (pid,)).fetchone()["author_id"]
    # 直接塞一条附件记录（连同磁盘文件），省得在测试里拼 multipart
    aid = c.execute(
        "INSERT INTO attachments (owner_id, board_id, attachable_type, attachable_id,"
        " orig_name, stored_path, ext, mime, size_bytes, sha256, created_at)"
        " VALUES (?,?,'post',?,?,?,?,?,?,?,?)",
        (owner, board, pid, "回收站夹具.txt", "2026/10/" + fname, "txt",
         "text/plain", 21, "x", 1)).lastrowid
    c.commit()

    ld = Client()
    check("回收站：组长登录", ld.login(leader["username"], PW))
    code, _ = mem.get("/b/%d/trash" % board)
    check("回收站：组员访问被拒（403）", code == 403, code)

    # ---- 附件是跟着宿主的：回复一删，附件也下不了 ----
    code, html = ld.get("/t/%d" % tid)
    check("回收站：删前附件在页面上", "回收站夹具.txt" in html)
    check("回收站：删前附件可下载", ld.get("/f/%d" % aid)[0] == 200)

    ld.post("/p/%d/delete" % pid, {})
    code, html = ld.get("/t/%d" % tid)
    check("回收站：删回复后讨论区看不到", rmark not in html)
    check("回收站：删回复后附件跟着隐藏", "回收站夹具.txt" not in html)
    check("回收站：删回复后附件下载 404", ld.get("/f/%d" % aid)[0] == 404)
    p = c.execute("SELECT is_deleted, body, deleted_at FROM posts WHERE id = ?", (pid,)).fetchone()
    check("回收站：回复软删、正文保留",
          p["is_deleted"] == 1 and p["body"] == rmark and p["deleted_at"], dict(p))
    code, html = ld.get("/b/%d/trash" % board)
    check("回收站：回复出现在回收站列表", rmark in html)

    # ---- 恢复回复 = 附件一起回来 ----
    ld.post("/trash/post/%d/restore" % pid, {})
    code, html = ld.get("/t/%d" % tid)
    check("回收站：回复已恢复", rmark in html)
    check("回收站：附件跟着回来", "回收站夹具.txt" in html)
    check("回收站：附件又能下载", ld.get("/f/%d" % aid)[0] == 200)

    # ---- 单独删附件 ----
    ld.post("/f/%d/delete" % aid, {})
    check("回收站：单独删的附件 404", ld.get("/f/%d" % aid)[0] == 404)
    code, html = ld.get("/b/%d/trash" % board)
    check("回收站：附件出现在回收站列表", "回收站夹具.txt" in html)
    ld.post("/trash/attach/%d/restore" % aid, {})
    check("回收站：附件单独恢复成功", ld.get("/f/%d" % aid)[0] == 200)

    # ---- 话题整体进出 ----
    # 搜索断言只认「结果链接」，别拿标题词去判整页 —— 搜索框会把 q 原样回显，
    # 词一定在页面里，那样断言必然误报（老坑）。
    code, html = ld.get("/search?q=%s" % tmark)
    check("回收站：删前搜得到", ("/t/%d" % tid) in html)
    ld.post("/t/%d/delete" % tid, {})
    check("回收站：删话题后详情 404", ld.get("/t/%d" % tid)[0] == 404)
    code, html = ld.get("/b/%d" % board)
    check("回收站：删话题后列表里没有它", tmark not in html)
    code, html = ld.get("/search?q=%s" % tmark)
    check("回收站：删话题后搜索结果里没有它", ("/t/%d" % tid) not in html)
    code, html = ld.get("/b/%d/trash" % board)
    check("回收站：话题出现在回收站列表", tmark in html)
    row = c.execute("SELECT is_deleted, deleted_at, deleted_by FROM topics WHERE id = ?",
                    (tid,)).fetchone()
    check("回收站：话题是软删（记录还在）",
          row["is_deleted"] == 1 and row["deleted_at"] and row["deleted_by"], dict(row))

    ld.post("/trash/topic/%d/restore" % tid, {})
    check("回收站：恢复话题后详情可访问", ld.get("/t/%d" % tid)[0] == 200)
    code, html = ld.get("/t/%d" % tid)
    check("回收站：恢复话题后讨论与附件都回来",
          rmark in html and "回收站夹具.txt" in html)

    # ---- 彻底删除：记录 + 磁盘文件一起清 ----
    ld.post("/t/%d/delete" % tid, {})
    ld.post("/trash/topic/%d/purge" % tid, {})
    check("回收站：彻底删除后话题记录没了",
          c.execute("SELECT COUNT(*) FROM topics WHERE id = ?", (tid,)).fetchone()[0] == 0)
    check("回收站：彻底删除后回复记录没了",
          c.execute("SELECT COUNT(*) FROM posts WHERE id = ?", (pid,)).fetchone()[0] == 0)
    check("回收站：彻底删除后附件记录没了",
          c.execute("SELECT COUNT(*) FROM attachments WHERE id = ?", (aid,)).fetchone()[0] == 0)
    check("回收站：彻底删除后磁盘文件没了", not fpath.exists())


def case_todos(c, t, leader, board, mem):
    """行动研究待办事项：置顶入口、权限、增改删、勾选下沉 / 取消恢复、拖动重排。

    这是「待办清单」的回归。核心几条：
      · 入口必须出现在帖子列表**上方**（所有人可见可点）
      · 只有管理员能增/改/删/排（组员 POST 一律 403）
      · 勾选完成 → 沉到末尾；取消勾选 → 回到原来的优先级位置
      · 拖动重排后 sort_order 连续，页面编号跟着显示顺序走
    """
    ad = Client()
    check("待办：管理员登录", ad.login("admin", ADMIN_PASSWORD))

    tag = "TODO-%s" % TAG
    A, B, Cc = tag + "-A", tag + "-B", tag + "-C"
    c.execute("DELETE FROM action_todos WHERE content LIKE ?", (tag + "%",))
    c.commit()

    def rows():
        return [dict(r) for r in c.execute(
            "SELECT id, content, due_date, is_done, sort_order FROM action_todos"
            " WHERE content LIKE ?"
            " ORDER BY is_done,"
            "   CASE WHEN is_done = 0 THEN sort_order ELSE COALESCE(done_at, 0) END, id",
            (tag + "%",))]

    def by(key):
        for r in rows():
            if r["content"] == key:
                return r
        return None

    # ---- 置顶入口：帖子列表最上方 ----
    code, html = ad.get("/b/%d" % board)
    check("待办：课题组页有置顶入口", code == 200 and 'class="todo-pin"' in html, "code=%s" % code)
    check("待办：置顶入口在帖子列表上方",
          'class="todo-pin"' in html and 'class="tlist"' in html
          and html.index('class="todo-pin"') < html.index('class="tlist"'))
    code, html = mem.get("/plaza")
    check("待办：广场页也有置顶入口", code == 200 and 'class="todo-pin"' in html)

    # ---- 权限：组员只看不改 ----
    code, _ = mem.post("/todos/add", {"content": tag + "-X", "due_y": ""})
    check("待办：组员添加被拒（403）", code == 403, code)
    code, html = mem.get("/todos")
    check("待办：组员能打开清单", code == 200 and "行动研究待办事项" in html, "code=%s" % code)
    check("待办：组员页面上没有操作控件",
          "todo-add" not in html and 'draggable="true"' not in html)

    # ---- 添加 ----
    for content in (A, B, Cc):
        st, _ = ad.post("/todos/add", {"content": content, "due_y": "", "due_m": "", "due_d": ""})
    check("待办：管理员添加返回 302", st == 302, st)
    check("待办：新条目接在末尾", [r["content"] for r in rows()] == [A, B, Cc],
          [r["content"] for r in rows()])
    check("待办：不选年份 = 暂不限期",
          all(r["due_date"] is None for r in rows()), [r["due_date"] for r in rows()])

    # 截止日期：选了年月日 → YYYY-MM-DD；「日」越界自动夹到月末
    ad.post("/todos/%d/edit" % by(B)["id"],
            {"content": B, "due_y": "2031", "due_m": "2", "due_d": "31"})
    check("待办：2 月 31 日自动夹到月末", by(B)["due_date"] == "2031-02-28", by(B)["due_date"])

    # ---- 勾选完成 → 沉底 ----
    ad.post("/todos/%d/toggle" % by(A)["id"], {})
    check("待办：勾选后标记完成", by(A)["is_done"] == 1, by(A)["is_done"])
    check("待办：完成项沉到列表末尾", [r["content"] for r in rows()] == [B, Cc, A],
          [r["content"] for r in rows()])
    code, html = ad.get("/todos")
    check("待办：完成项带灰化样式", "todo-item done" in html)

    # ---- 取消勾选 → 恢复未完成、回原顺位 ----
    ad.post("/todos/%d/toggle" % by(A)["id"], {})
    check("待办：取消勾选恢复未完成", by(A)["is_done"] == 0, by(A)["is_done"])
    check("待办：取消勾选后回到原来的优先级位置",
          [r["content"] for r in rows()] == [A, B, Cc], [r["content"] for r in rows()])

    # ---- 拖动排序：C → A → B，编号跟着重排 ----
    st, _ = ad.post("/todos/reorder",
                    {"order": [by(Cc)["id"], by(A)["id"], by(B)["id"]]})
    check("待办：拖动排序返回 302", st == 302, st)
    check("待办：拖动后按新优先级排列", [r["content"] for r in rows()] == [Cc, A, B],
          [r["content"] for r in rows()])
    check("待办：重排后 sort_order 连续", [r["sort_order"] for r in rows()] == [0, 1, 2],
          [r["sort_order"] for r in rows()])
    code, html = ad.get("/todos")
    check("待办：页面编号按显示顺序现算",
          all(('class="todo-no">%d</span>' % i) in html for i in (1, 2, 3)))

    # ---- ↑ / ↓ 兜底（拖动排序的无 JS 版本）----
    ad.post("/todos/%d/move" % by(B)["id"], {"dir": "up"})
    check("待办：下移按钮把 B 提到 A 前", [r["content"] for r in rows()] == [Cc, B, A],
          [r["content"] for r in rows()])

    # ---- 伪造拖动请求：已完成/不存在/重复的 id 都要被挡掉 ----
    ad.post("/todos/%d/toggle" % by(A)["id"], {})          # A 变已完成
    ad.post("/todos/reorder", {"order": [by(A)["id"], by(Cc)["id"], by(Cc)["id"], "999999"]})
    check("待办：拖动请求里的已完成/重复/无效 id 被忽略",
          [r["content"] for r in rows() if r["is_done"] == 0] == [Cc, B],
          [r["content"] for r in rows()])
    ad.post("/todos/%d/toggle" % by(A)["id"], {})          # 还原

    # ---- 编辑 ----
    ad.post("/todos/%d/edit" % by(Cc)["id"],
            {"content": Cc + "-改", "due_y": "2032", "due_m": "3", "due_d": "9"})
    check("待办：编辑改内容与截止日期",
          by(Cc + "-改") is not None and by(Cc + "-改")["due_date"] == "2032-03-09",
          by(Cc + "-改"))

    # ---- 删除 ----
    ad.post("/todos/%d/delete" % by(A)["id"], {})
    check("待办：删除后条目没了", by(A) is None, [r["content"] for r in rows()])

    # ---- 收尾：清掉本用例的数据 ----
    c.execute("DELETE FROM action_todos WHERE content LIKE ?", (tag + "%",))
    c.commit()
    check("待办：用例数据已清理", rows() == [], rows())


def main():
    if not DB.exists():
        sys.exit("[夹具] 数据库不存在：%s\n请先跑 tools/seed_demo.py --reset" % DB)
    c = con()
    t, asg, leader = resolve_fixture(c)
    print("话题 #%s  组员 %s / 组长 %s" % (t["id"], asg["username"], leader["username"]))

    print("\n-- 1. 提交 / 删除回退（B）--")
    mem, sid = case_submit_and_delete(c, t, asg, leader)
    print("\n-- 2. 重新提交（C）--")
    case_resubmit(c, t, asg, mem, sid)
    print("\n-- 3. 楼中楼孤儿 --")
    case_orphan(c, t, mem)
    print("\n-- 4. 确认弹窗接线 --")
    case_confirm_wiring(c, t, leader, t["board_id"])
    print("\n-- 5. 回收站 --")
    case_trash(c, t, leader, t["board_id"], mem)
    print("\n-- 6. 行动研究待办 --")
    case_todos(c, t, leader, t["board_id"], mem)

    print("\n" + "=" * 60)
    print("通过 %d 项，失败 %d 项" % (len(OK), len(BAD)))
    if BAD:
        print("失败清单：")
        for b in BAD:
            print("   - " + b)
    print("=" * 60)
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
