# -*- coding: utf-8 -*-
"""全站冒烟测试：登录 -> 遍历所有页面 -> 检查状态码。

设计要点：所有 id 都从数据库里现查，绝不写死 /b/1、/t/1。
这样无论演示数据被 --reset 过几次、自增 id 漂到多少，测试都成立。
"""
import http.cookiejar
import io
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

BASE = os.environ.get("BBS_SMOKE_BASE", "http://127.0.0.1:8009")
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("BBS_DATA_DIR", str(ROOT / "data"))) / "bbs.db"

# 管理员密码：线上实例的 admin 密码一旦被改过，写死的 "admin123" 就会让第 9 组用例
# 整片失败（假故障）。所以允许用 BBS_ADMIN_PASSWORD 覆盖，默认仍是初始密码。
ADMIN_PASSWORD = os.environ.get("BBS_ADMIN_PASSWORD", "admin123")


# --------------------------------------------------------------------------
# 夹具：从库里把这次要打的 id 全部查出来
# --------------------------------------------------------------------------
def _con():
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def _one(cur, sql, params=(), label=""):
    row = cur.execute(sql, params).fetchone()
    if row is None:
        raise SystemExit(f"[夹具] 找不到需要的演示数据：{label or sql}")
    return row[0]


def resolve_fixture():
    if not DB_PATH.exists():
        raise SystemExit(f"[夹具] 数据库不存在：{DB_PATH}\n请先跑 seed_demo.py 灌演示数据。")
    con = _con()
    cur = con.cursor()

    fx = {}
    fx["B1"] = _one(cur, "SELECT id FROM boards WHERE name LIKE '核心素养%' ORDER BY id LIMIT 1",
                    label="课题一（核心素养）")
    fx["B2"] = _one(cur, "SELECT id FROM boards WHERE name LIKE '基于 Python%' ORDER BY id LIMIT 1",
                    label="课题二（Python）")
    fx["B3"] = _one(cur, "SELECT id FROM boards WHERE name LIKE '信息组日常教研%' ORDER BY id LIMIT 1",
                    label="课题三（日常教研）")

    fx["T1"] = _one(cur, "SELECT id FROM topics WHERE title LIKE '撰写《项目式学习课例》%' LIMIT 1",
                    label="话题：课例初稿（任务）")
    fx["T2"] = _one(cur, "SELECT id FROM topics WHERE title LIKE '关于 10 月中期检查%' LIMIT 1",
                    label="话题：中期材料清单（公告）")
    fx["T3"] = _one(cur, "SELECT id FROM topics WHERE title LIKE '课例框架讨论%' LIMIT 1",
                    label="话题：课例框架讨论")
    fx["T4"] = _one(cur, "SELECT id FROM topics WHERE title LIKE '提交学生问卷原始数据%' LIMIT 1",
                    label="话题：问卷数据（任务）")
    fx["T5"] = _one(cur, "SELECT id FROM topics WHERE title LIKE '整理中期教学案例%' LIMIT 1",
                    label="话题：课题二话题（张老师非成员）")

    fx["ATT"] = _one(cur, "SELECT id FROM attachments ORDER BY id LIMIT 1", label="任意一个附件")

    # 审核表单里的 assignee_id 是 task_assignees.id，取一条「已提交待审」的
    fx["ASSIGNEE"] = _one(
        cur,
        "SELECT ta.id FROM task_assignees ta JOIN tasks t ON t.id = ta.task_id"
        " WHERE t.topic_id = ? AND ta.status = 'done' ORDER BY ta.id LIMIT 1",
        (fx["T1"],), label="话题一里待审的提交")
    fx["ASSIGNEE_NAME"] = _one(
        cur,
        "SELECT u.display_name FROM task_assignees ta"
        " JOIN users u ON u.id = ta.user_id WHERE ta.id = ?",
        (fx["ASSIGNEE"],))

    # 指派用的 assignee_ids 是用户 id
    fx["ASSIGN_USERS"] = [
        _one(cur, "SELECT id FROM users WHERE username = ?", (u,), label=f"用户 {u}")
        for u in ("wuls", "lils", "chenls", "wangls")
    ]

    # 删除账号用：组长（删了会没人带队）和「有内容但不是组长」（删了记录会断）
    fx["LEADER"] = _one(cur, "SELECT id FROM users WHERE username = 'zhangls'", label="张老师")
    fx["MEMBER_CONTENT"] = _one(cur, "SELECT id FROM users WHERE username = 'chenls'", label="陈老师")

    con.close()
    return fx


FX = resolve_fixture()
B1, B2, B3 = FX["B1"], FX["B2"], FX["B3"]
T1, T2, T3, T4, T5 = FX["T1"], FX["T2"], FX["T3"], FX["T4"], FX["T5"]

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
opener.addheaders = [("User-Agent", "smoke-test")]

ok, bad = [], []


def get(path, expect=200, label=None):
    url = BASE + path
    try:
        with opener.open(url, timeout=20) as r:
            code = r.status
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code = e.code
        body = e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        code = 0
        body = str(e)
    name = label or path
    if code == expect:
        ok.append((name, code))
    else:
        snippet = re.sub(r"\s+", " ", body)
        bad.append((name, code, snippet[:400]))
    return code, body


def csrf_from(html):
    m = re.search(r'name="_csrf" value="([^"]+)"', html)
    return m.group(1) if m else None


def post(path, data, expect=302, label=None):
    url = BASE + path
    body_bytes = urllib.parse.urlencode(data, doseq=True).encode()
    req = urllib.request.Request(url, data=body_bytes, method="POST")
    try:
        with opener.open(req, timeout=30) as r:
            code, text, loc = r.status, r.read().decode("utf-8", "replace"), r.headers.get("Location")
    except urllib.error.HTTPError as e:
        code, text, loc = e.code, e.read().decode("utf-8", "replace"), e.headers.get("Location")
    except Exception as e:  # noqa: BLE001
        code, text, loc = 0, str(e), None
    name = label or f"POST {path}"
    if code == expect:
        ok.append((name, code))
    else:
        bad.append((name, code, re.sub(r"\s+", " ", text)[:400]))
    return code, text, loc


print(f"[夹具] B1={B1} B2={B2} B3={B3} T1={T1} T2={T2} T3={T3} T4={T4} T5={T5} "
      f"ATT={FX['ATT']} ASSIGNEE={FX['ASSIGNEE']}({FX['ASSIGNEE_NAME']})")

# ---------- 1. 登录页 ----------
code, html = get("/login", 200, "登录页")
if code == 200:
    tok = csrf_from(html)
    if not tok:
        bad.append(("登录页缺少 csrf token", 0, html[:300]))

# ---------- 2. 未登录时应重定向 ----------
noredirect = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    type("NoRedirect", (urllib.request.HTTPRedirectHandler,), {
        "redirect_request": lambda *a, **k: None})())
try:
    with noredirect.open(BASE + "/plaza", timeout=10) as r:
        bad.append(("未登录访问 /plaza 竟然成功了", r.status, ""))
except urllib.error.HTTPError as e:
    if e.code == 302:
        ok.append(("未登录访问 /plaza 被重定向到登录页", 302))
    else:
        bad.append(("未登录访问 /plaza", e.code, str(e)))

# ---------- 3. 登录 ----------
code, html = get("/login", 200)
tok = csrf_from(html)
code, html, _ = post("/login", {"_csrf": tok, "username": "zhangls", "password": "123456", "next": ""},
                     200, "登录 张老师")
if "课题组广场" in html and "张老师" in html:
    ok.append(("登录后进入广场且带用户名", 200))
else:
    bad.append(("登录后页面不对", code, html[:300]))

# ---------- 4. 遍历页面 ----------
pages = [
    ("/", 200, "根路径重定向到广场"),
    ("/plaza", 200, "广场·全部"),
    ("/plaza?scope=mine", 200, "广场·我参与的"),
    ("/plaza?scope=public", 200, "广场·公开组"),
    (f"/b/{B1}", 200, "课题组 1"),
    (f"/b/{B1}?kind=task", 200, "课题组 1·任务筛选"),
    (f"/b/{B1}?sort=new", 200, "课题组 1·按时间"),
    (f"/b/{B1}?kind=discussion&sort=hot", 200, "课题组 1·讨论最热"),
    # 其余类型的筛选页在下面按 config.TOPIC_KIND_ORDER 自动补全，加类型后不用改这里
    (f"/b/{B1}/files", 200, "课题组 1·文件库"),
    (f"/b/{B1}/settings", 200, "课题组 1·组设置"),
    (f"/b/{B1}/new", 200, "发帖页"),
    ("/b/new", 200, "创建课题组页"),
    (f"/b/{B3}", 200, "课题组 3（张老师是成员）"),
    (f"/t/{T1}", 200, "话题 1（任务）"),
    (f"/t/{T1}/edit", 200, "话题 1·编辑"),
    (f"/t/{T2}", 200, "话题 2（公告）"),
    (f"/t/{T3}", 200, "话题 3（讨论）"),
    (f"/t/{T4}", 200, "话题 4（任务）"),
    ("/my/tasks", 200, "我的任务"),
    ("/my/tasks?scope=done", 200, "我的任务·已提交"),
    ("/my/tasks?scope=all", 200, "我的任务·全部"),
    ("/notifications", 200, "通知中心"),
    ("/notifications?scope=unread", 200, "通知·未读"),
    ("/me", 200, "个人设置"),
    ("/search?q=%E8%AF%BE%E4%BE%8B", 200, "搜索·话题"),
    ("/search?q=%E8%AF%BE%E4%BE%8B&scope=posts", 200, "搜索·回复"),
    ("/search?q=.txt&scope=files", 200, "搜索·附件"),
    ("/healthz", 200, "健康检查"),
    (f"/f/{FX['ATT']}", 200, "附件下载"),
    (f"/f/{FX['ATT']}/inline", 200, "附件预览"),
]
for path, exp, label in pages:
    get(path, exp, label)

# ---------- 4.5 话题类型：标签栏 / 筛选 / 发帖选项 ----------
# 类型清单存在数据库里（管理员在后台可自助增删），所以测试也从库里读，不写死、不看 config。
def db_kinds(active_only=False):
    con = _con()
    sql = ("SELECT code, label, color, leader_only, pinnable, is_active, is_builtin"
           " FROM topic_kinds")
    if active_only:
        sql += " WHERE is_active = 1"
    rows = [dict(r) for r in con.execute(sql + " ORDER BY sort_order, id")]
    con.close()
    return rows


if not db_kinds():
    raise SystemExit("[夹具] topic_kinds 表是空的 —— 服务启动时会自动灌种子，先启动一次服务。")

KINDS = db_kinds()
ACTIVE = [k for k in KINDS if k["is_active"]]
LBL = {k["code"]: k["label"] for k in KINDS}
LEADER_ONLY = tuple(k["code"] for k in KINDS if k["leader_only"])
FREE_LABELS = [k["label"] for k in ACTIVE if not k["leader_only"]]


def assert_true(cond, label, detail=""):
    if cond:
        ok.append((label, 200))
    else:
        bad.append((label, 0, detail))


_code, _bp = get(f"/b/{B1}", 200)
for _k in ACTIVE:
    get(f"/b/{B1}?kind={_k['code']}", 200, f"课题组 1·{_k['label']}筛选")
    assert_true(f"kind={_k['code']}" in _bp, f"课题组页标签栏有「{_k['label']}」", _k["code"])
    assert_true(f">{_k['label']}</a>" in _bp, f"课题组页有「{_k['label']}」链接文字", _k["label"])
assert_true("t-c-" in _bp, "类型标签用调色板类名渲染（后台换颜色即时生效，无需改 CSS）")

_code, _np = get(f"/b/{B1}/new", 200)
for _k in ACTIVE:
    assert_true(f'value="{_k["code"]}"' in _np, f"发帖页有「{_k['label']}」选项", _k["code"])
assert_true("data-uploader" in _np, "发帖页自带附件上传区（与类型无关）")

# ---------- 4.6 每种类型都能真的传上附件（发帖 -> 上传 -> 认领 -> 附件挂在话题上） ----------


def upload_file(board_id, filename, content, token, kind=None, ctype="text/plain"):
    """按前端的 multipart 约定往 /upload 传一个文件，返回 (状态码, json)。

    kind="image" + 位图 ctype 走「正文插图」那条路（服务端只收位图）；
    默认不传 kind，就是普通附件上传。
    """
    if not token:
        # 页面没加载出来（服务没起 / 被踢回登录页）时别抛异常，让用例如实报失败
        return 0, {"raw": "没拿到 csrf token，发帖页可能没打开"}
    bd = "----smoke7788boundary"
    buf = []
    fields = [("board_id", str(board_id)), ("attachable_type", "draft"), ("attachable_id", "0")]
    if kind:
        fields.append(("kind", kind))
    for k, v in fields:
        buf.append(f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    buf.append(f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
               f'Content-Type: {ctype}\r\n\r\n'.encode() + content + b"\r\n")
    buf.append(f"--{bd}--\r\n".encode())
    rq = urllib.request.Request(BASE + "/upload", data=b"".join(buf), method="POST")
    rq.add_header("Content-Type", f"multipart/form-data; boundary={bd}")
    rq.add_header("X-CSRF-Token", token)
    try:
        with opener.open(rq, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, {"raw": raw[:200]}


for _k in [k["code"] for k in ACTIVE if not k.get("is_builtin", 0)]:
    _lb = LBL[_k]
    _ttl = f"冒烟测试·{_lb}（可删）"
    _, _h = get(f"/b/{B1}/new", 200)
    _tok = csrf_from(_h)
    _ust, _ures = upload_file(B1, f"smoke-{_k}.txt", f"{_k} 附件内容".encode(), _tok)
    if _ust != 200 or not _ures.get("ok"):
        bad.append((f"「{_lb}」上传附件", _ust, str(_ures)[:200]))
        continue
    _aid = _ures["id"]
    post(f"/b/{B1}/new", {"_csrf": _tok, "kind": _k, "title": _ttl,
                          "body": f"冒烟测试：验证「{_lb}」能发布且能挂附件。",
                          "attach_ids": [str(_aid)]},
         200, f"发布「{_lb}」话题并带附件")

    _c = _con()
    _row = _c.execute("SELECT t.id, t.kind FROM topics t WHERE t.title = ? ORDER BY t.id DESC LIMIT 1",
                      (_ttl,)).fetchone()
    _att = _c.execute("SELECT attachable_type, attachable_id, board_id FROM attachments WHERE id = ?",
                      (_aid,)).fetchone()
    _c.close()
    if _row is None:
        bad.append((f"「{_lb}」话题没建出来", 0, _ttl))
        continue
    assert_true(_row["kind"] == _k, f"「{_lb}」入库 kind 正确", _row["kind"])
    assert_true(_att is not None and _att["attachable_type"] == "topic"
                and _att["attachable_id"] == _row["id"],
                f"「{_lb}」附件已认领到该话题", str(dict(_att) if _att else None))
    _dcode, _dh = get(f"/t/{_row['id']}", 200)
    assert_true(f"smoke-{_k}.txt" in _dh, f"「{_lb}」详情页能看到附件", f"smoke-{_k}.txt")
    assert_true("data-uploader" in _dh, f"「{_lb}」详情页可继续传附件")

print()

# ---------- 5. 权限：张老师不是课题组 2 的成员 ----------
get(f"/b/{B2}", 403, "非成员访问课题组 2 → 403")
get(f"/t/{T5}", 403, "非成员访问课题组 2 的话题 → 403")
get(f"/b/{B2}/new", 403, "非成员发帖被拒 → 403")
get(f"/b/{B1}/settings", 200, "组长可进组设置")

# ---------- 6. 写操作：回复 / 发帖 / 审核打分 ----------
code, html = get(f"/t/{T3}", 200)
tok = csrf_from(html)
post(f"/t/{T3}/reply", {"_csrf": tok, "body": "冒烟测试：这是一条自动生成的回复。"}, 200, "发表回复")

code, html = get(f"/b/{B1}/new", 200)
tok = csrf_from(html)
post(f"/b/{B1}/new", {"_csrf": tok, "kind": "discussion", "title": "冒烟测试话题（可删）",
                      "body": "自动生成的测试话题。"}, 200, "发布讨论话题")

code, html = get(f"/t/{T1}", 200)
m = re.search(r'name="assignee_id" value="(\d+)"', html)
if m:
    tok = csrf_from(html)
    post(f"/t/{T1}/review", {"_csrf": tok, "assignee_id": m.group(1), "action": "confirm",
                             "score": "9", "review_note": "冒烟测试评分"}, 200, "组长审核通过并打分")
else:
    bad.append(("找不到可审核的提交", 0, f"话题 {T1} 里没有待审记录"))

code, html = get(f"/t/{T4}", 200)
tok = csrf_from(html)
post(f"/t/{T4}/assign", {"_csrf": tok, "assignee_ids": [str(u) for u in FX["ASSIGN_USERS"]]},
     200, "调整任务指派")

# ---------- 6.5 任务说明不与正文重复（「任务即话题」：同源时只显示一次） ----------

def task_detail_of(html):
    """取任务卡里那段说明文字；取不到返回 None。"""
    m = re.search(r'<section class="card taskcard" id="task">(.*?)</section>', html, re.S)
    if not m:
        return None
    d = re.search(r'<div class="body-text">(.*?)</div>', m.group(1), re.S)
    return d.group(1).strip() if d else None


MARK = "SMOKE-DEDUP-7788"
MADE_TITLE = "冒烟测试任务（可删）"
code, html = get(f"/b/{B1}/new", 200)
post(f"/b/{B1}/new", {"_csrf": csrf_from(html), "kind": "task", "title": MADE_TITLE,
                      "body": f"第一行要求 {MARK}\n第二行要求：交一份 Word。",
                      "points": "0", "due_date": "2030-01-01",
                      "assignee_ids": [str(FX["ASSIGN_USERS"][0])]}, 200, "发布任务话题")

_con2 = _con()
_made = _con2.execute("SELECT id FROM topics WHERE title = ? ORDER BY id DESC LIMIT 1",
                      (MADE_TITLE,)).fetchone()
_con2.close()
if _made is None:
    bad.append(("找不到刚发布的任务话题", 0, MADE_TITLE))
else:
    _, _th = get(f"/t/{_made['id']}", 200)
    _txt = task_detail_of(_th)
    if not _txt:
        bad.append(("任务卡里没有渲染出任务说明", 0, ""))
    elif _th.count(_txt) == 1:
        ok.append(("任务说明只显示 1 次（不与正文重复）", 200))
    else:
        bad.append((f"任务说明重复显示 {_th.count(_txt)} 次", 0, _txt[:80]))
    _main = re.search(r'<section class="card">(.*?)</section>', _th, re.S)
    if _main and MARK in _main.group(0):
        bad.append(("主帖卡里仍重复贴了任务说明", 0, ""))
    else:
        ok.append(("主帖卡不再重复任务说明", 200))

# 已有演示任务（正文 ≠ 任务说明）时，两段信息都应保留，不能丢
_, _t1h = get(f"/t/{T1}", 200)
if task_detail_of(_t1h) and task_detail_of(_t1h) in _t1h:
    ok.append(("正文与任务说明不同时，任务说明仍正常展示", 200))
else:
    bad.append(("演示任务的任务说明丢了", 0, ""))

# ---------- 7. 组长导出课题材料 ----------
for mode in ("all", "files"):
    url = f"{BASE}/b/{B1}/export?mode={mode}"
    try:
        with opener.open(url, timeout=60) as r:
            data = r.read()
            ctype = r.headers.get("Content-Type", "")
            if r.status == 200 and data[:2] == b"PK" and "zip" in ctype:
                ok.append((f"打包下载 mode={mode}（{len(data)} 字节）", 200))
            else:
                bad.append((f"打包下载 mode={mode}", r.status, f"{ctype} {len(data)}"))
    except Exception as e:  # noqa: BLE001
        bad.append((f"打包下载 mode={mode}", 0, str(e)))

# ---------- 8. 错误页 ----------
get("/t/99999", 404, "不存在的话题 → 404")
get("/no-such-page", 404, "不存在的路径 → 404")

# ---------- 9. 管理员页面（先退出再换账号） ----------
code, html = get("/plaza", 200)
tok = csrf_from(html)
post("/logout", {"_csrf": tok}, 200, "退出登录")
code, html = get("/login", 200)
tok = csrf_from(html)
code, html, _ = post("/login", {"_csrf": tok, "username": "admin", "password": ADMIN_PASSWORD, "next": ""},
                     200, "登录 管理员")
for path, label in [("/admin/", "后台首页"), ("/admin/users", "账号管理"),
                    ("/admin/users?q=%E5%BC%A0", "账号搜索"), ("/admin/import", "批量导入"),
                    ("/admin/boards", "课题组总览")]:
    get(path, 200, label)

# 新建账号 + 批量导入：随机后缀，避免重复跑时撞唯一约束
import random  # noqa: E402
suffix = f"{random.randint(1000, 9999)}"
code, html = get("/admin/users", 200)
tok = csrf_from(html)
post("/admin/users/new", {"_csrf": tok, "username": f"smoketest{suffix}", "display_name": "冒烟老师",
                          "subject": "信息科技", "password": "123456"}, 200, "新建账号")
code, html = get("/admin/import", 200)
tok = csrf_from(html)
post("/admin/import", {"_csrf": tok,
                       "text": f"冒烟二甲,smoke2{suffix},123456,信息科技\n冒烟三乙,smoke3{suffix}",
                       "default_password": "123456"}, 200, "批量导入账号")

# ---------- 10. 账号删除 ----------
# 规则：没发过任何内容的账号能真删；组长、或名下有内容的，必须被拦下。
FLASH = re.compile(r'<div class="flash flash-\w+">(.*?)</div>', re.S)


def flashes(html):
    return re.sub(r"\s+", " ", " | ".join(FLASH.findall(html))).strip()


deluser = f"smokedel{suffix}"
code, html = get("/admin/users", 200)
post("/admin/users/new", {"_csrf": csrf_from(html), "username": deluser,
                          "display_name": "冒烟待删", "subject": "信息科技",
                          "password": "123456"}, 200, "新建一个待删账号")

con = _con()
r = con.execute("SELECT id FROM users WHERE username = ?", (deluser,)).fetchone()
con.close()
if r is None:
    bad.append(("待删账号没建出来", 0, deluser))
else:
    did = r["id"]
    code, html = get("/admin/users", 200)
    post(f"/admin/users/{did}/delete", {"_csrf": csrf_from(html)}, 200, "删除干净账号")
    # 注意：搜索关键词会回显在 <input name="q" value="..."> 里，
    # 所以不能用「全文是否含该字符串」判断，得看用户行还在不在。
    _, after = get(f"/admin/users?q={deluser}", 200, "删除后按账号搜索")
    if f"账号 {deluser} ·" not in after:
        ok.append(("干净账号确实被删掉了", 200))
    else:
        bad.append(("干净账号没删掉", 0, deluser))

    # 组长不能删
    code, html = get("/admin/users", 200)
    _, html, _ = post(f"/admin/users/{FX['LEADER']}/delete", {"_csrf": csrf_from(html)},
                      200, "删除组长（应被拦）")
    if "组长" in flashes(html) and f'id="u{FX["LEADER"]}"' in html:
        ok.append(("组长账号被正确拦下且仍在列表", 200))
    else:
        bad.append(("组长账号没拦住", 0, flashes(html)[:200]))

    # 有内容但不是组长：也不能删
    code, html = get("/admin/users", 200)
    _, html, _ = post(f"/admin/users/{FX['MEMBER_CONTENT']}/delete", {"_csrf": csrf_from(html)},
                      200, "删除有内容的组员（应被拦）")
    if "名下还有" in flashes(html) and f'id="u{FX["MEMBER_CONTENT"]}"' in html:
        ok.append(("有内容的组员被正确拦下（引导用停用）", 200))
    else:
        bad.append(("有内容的组员没拦住", 0, flashes(html)[:200]))

    # 自己不能删自己
    con2 = _con()
    me_id = _one(con2.cursor(), "SELECT id FROM users WHERE username = 'admin'", label="admin")
    con2.close()
    code, html = get("/admin/users", 200)
    _, html, _ = post(f"/admin/users/{me_id}/delete", {"_csrf": csrf_from(html)}, 200, "删除自己（应被拦）")
    if "不能删除自己" in flashes(html):
        ok.append(("不能删除自己", 200))
    else:
        bad.append(("删除自己没拦住", 0, flashes(html)[:200]))

    # 改登录账号：能改，且重名会被拒
    code, html = get("/admin/users", 200)
    _, html, _ = post(f"/admin/users/{FX['LEADER']}/update",
                      {"_csrf": csrf_from(html), "display_name": "张老师",
                       "username": "admin", "subject": "信息技术", "role": "teacher"},
                      200, "把登录账号改成已存在的（应被拒）")
    if "占用" in flashes(html):
        ok.append(("改登录账号时重名被拒", 200))
    else:
        bad.append(("重名的登录账号没拦住", 0, flashes(html)[:200]))

# ---------- 11. 组员视角：只看得到能发的类型，任务/公告被拒 ----------
post("/logout", {"_csrf": csrf_from(get("/me", 200)[1])}, 200, "退出登录")
_, _lg = get("/login", 200)
post("/login", {"_csrf": csrf_from(_lg), "username": "wuls", "password": "123456", "next": ""},
     200, "登录 吴老师（组员）")

_, _mh = get(f"/b/{B1}/new", 200)
assert_true('id="taskFields"' not in _mh, "组员发帖页没有组长专属字段")
for _k in ACTIVE:
    if _k["code"] in LEADER_ONLY:
        assert_true(f'value="{_k["code"]}"' not in _mh, f"组员发帖页看不到「{_k['label']}」", _k["code"])
    else:
        assert_true(f'value="{_k["code"]}"' in _mh, f"组员发帖页有「{_k['label']}」", _k["code"])
_hm = re.search(r'class="hint">\s*(.*?)\s*</span>', _mh, re.S)
_hint = re.sub(r"\s+", " ", _hm.group(1)) if _hm else ""
assert_true(_hint and "undefined" not in _hint, "组员提示语正常渲染（自动列出可发类型）", _hint[:120])
assert_true(all(lb in _hint for lb in FREE_LABELS),
            "组员提示语列全了可发类型", _hint[:120])

# 越权发任务/公告要被服务端拦住（前端藏了不算数）
for _k in LEADER_ONLY:
    post(f"/b/{B1}/new", {"_csrf": csrf_from(_mh), "kind": _k, "title": f"越权{_k}",
                          "body": "x"}, 403, f"组员发「{LBL.get(_k, _k)}」→ 403")

# 组员发一个「全员可发」的类型 + 传附件，应成功。类型从库里挑，不写死。
_MK = next((k for k in ACTIVE if not k["leader_only"] and not k["is_builtin"]), None)
if _MK is None:
    bad.append(("找不到组员可发的类型（库里没有非内置且非组长专属的类型）", 0, ""))
else:
    _mttl = f"冒烟测试·组员{_MK['label']}（可删）"
    _, _mh = get(f"/b/{B1}/new", 200)
    _mtok = csrf_from(_mh)
    _ust, _ures = upload_file(B1, "smoke-member-start.txt", "组员上传的附件".encode(), _mtok)
    assert_true(_ust == 200 and _ures.get("ok"), "组员在发帖页上传附件", str(_ures)[:120])
    if _ust == 200 and _ures.get("ok"):
        post(f"/b/{B1}/new", {"_csrf": _mtok, "kind": _MK["code"], "title": _mttl,
                              "body": f"组员发起的{_MK['label']}，带附件。",
                              "attach_ids": [str(_ures["id"])]},
             200, f"组员发布「{_MK['label']}」并带附件")
        _c = _con()
        _rr = _c.execute("SELECT id FROM topics WHERE title = ? ORDER BY id DESC LIMIT 1",
                         (_mttl,)).fetchone()
        _c.close()
        if _rr:
            _, _dh = get(f"/t/{_rr['id']}", 200)
            assert_true("smoke-member-start.txt" in _dh, f"组员发的「{_MK['label']}」详情页能看到附件")
        else:
            bad.append((f"组员发的「{_MK['label']}」没建出来", 0, ""))

# ---------- 12. 管理后台：类型标签自助增删 ----------
# 本次核心能力：加/改/停/删标签都在后台点几下完成，不用改代码、不用重新部署。
# 注意 admin 不是课题组 1 的成员，所以「看标签栏 / 发帖」的断言要换成张老师（B1 组长）来做。

def switch_to(username, password, note=""):
    """切账号：先退出再登录，避免登录态残留导致后面全片误判。"""
    _, _me = get("/me", 200)
    post("/logout", {"_csrf": csrf_from(_me)}, 200, f"退出登录（切到{note or username}）")
    _, _lg2 = get("/login", 200)
    post("/login", {"_csrf": csrf_from(_lg2), "username": username, "password": password, "next": ""},
         200, f"登录 {note or username}")


def kinds_db():
    """直接读库看类型表当前状态（后台操作是否真的落库）。"""
    c = _con()
    rows = [dict(r) for r in c.execute(
        "SELECT id, code, label, color, leader_only, is_active, sort_order"
        " FROM topic_kinds ORDER BY sort_order, id")]
    c.close()
    return rows


def kind_by_label(label):
    return next((r for r in kinds_db() if r["label"] == label), None)


def admin_token():
    return csrf_from(get("/admin/kinds", 200)[1])


_ksuf = random.randint(100, 999)
LAB_USED = f"冒烟标签{_ksuf}"          # 会被发一个话题，用来验证「有话题在用不许删」
LAB_UNUSED = f"冒烟空标签{_ksuf}"      # 没人用，用来验证「能真删」
LAB_LONG = "这个标签名字实在是太长了"
CODE_USED = f"smk{_ksuf}"

switch_to("admin", ADMIN_PASSWORD, "管理员（类型标签）")

_kh = get("/admin/kinds", 200)[1]
assert_true("类型标签" in _kh, "管理后台有「类型标签」页")
assert_true(all(k["label"] in _kh for k in KINDS), "标签管理页列出了现有全部类型")
assert_true("/admin/kinds/new" in _kh and "/move" in _kh, "标签管理页有新建与排序入口")

# ① 新建标签（指定颜色与网址标识）
_c, _r, _l = post("/admin/kinds/new", {"_csrf": admin_token(), "label": LAB_USED,
                                       "color": "purple", "code": CODE_USED},
                  200, "后台新建标签（指定颜色与标识）")
assert_true("已添加标签" in flashes(_r), "新建标签后给了明确提示", flashes(_r)[:80])
_made = kind_by_label(LAB_USED)
assert_true(_made is not None and _made["color"] == "purple", "新标签已入库且颜色正确",
            str(_made))

# ② 重名 / 超长名 / 非法标识 都要被拦下
_c, _r, _l = post("/admin/kinds/new", {"_csrf": admin_token(), "label": LAB_USED, "color": "ok"},
                  200, "新建重名标签（应被拒）")
assert_true("已经有一个叫" in flashes(_r), "重名标签被拒绝", flashes(_r)[:80])

post("/admin/kinds/new", {"_csrf": admin_token(), "label": LAB_LONG, "color": "ok"},
     200, "新建超长名称（应被拒）")
assert_true(kind_by_label(LAB_LONG) is None, "超长名称没被写进库")

post("/admin/kinds/new", {"_csrf": admin_token(), "label": f"冒烟非法{_ksuf}", "code": "Bad Code!"},
     200, "新建非法网址标识（应被拒）")
assert_true(kind_by_label(f"冒烟非法{_ksuf}") is None, "非法标识没被写进库")

# ③ 再建一个不会被使用的标签（验证真删）
post("/admin/kinds/new", {"_csrf": admin_token(), "label": LAB_UNUSED, "color": "rose"},
     200, "新建一个标签（标识留空，自动生成）")
_unused = kind_by_label(LAB_UNUSED)
assert_true(_unused is not None and _unused["code"].startswith("k"),
            "标识留空时自动生成了标识", str(_unused))

# ④ 新标签要立刻出现在标签栏 / 筛选页 / 发布页（张老师的视角）
switch_to("zhangls", "123456", "张老师（课题组 1 组长）")
_, _bp_new = get(f"/b/{B1}", 200)
assert_true(f">{LAB_USED}</a>" in _bp_new, "新标签立刻出现在课题组页标签栏")
assert_true(f">{LAB_UNUSED}</a>" in _bp_new, "第二个新标签也在标签栏里")
get(f"/b/{B1}?kind={CODE_USED}", 200, "新标签的筛选页可打开")
_, _np_new = get(f"/b/{B1}/new", 200)
assert_true(f'value="{CODE_USED}"' in _np_new, "新标签立刻出现在发布页的类型选择里")

# 用它发一个话题：颜色要按后台所选渲染
post(f"/b/{B1}/new", {"_csrf": csrf_from(_np_new), "kind": CODE_USED,
                      "title": f"冒烟测试·{LAB_USED}（可删）",
                      "body": "用于验证标签的颜色、改名与删除保护。"},
     200, f"用新标签「{LAB_USED}」发布话题")
_c = _con()
_new_topic = _c.execute("SELECT id FROM topics WHERE title = ? ORDER BY id DESC LIMIT 1",
                        (f"冒烟测试·{LAB_USED}（可删）",)).fetchone()
_c.close()
if _new_topic is None:
    bad.append(("用新标签发布的话题没建出来", 0, LAB_USED))
    NEW_TID = None
else:
    NEW_TID = _new_topic["id"]
    _, _td = get(f"/t/{NEW_TID}", 200)
    assert_true("t-c-purple" in _td, "新标签的话题按后台所选颜色显示（紫色）")
    assert_true(LAB_USED in _td, "话题详情页显示新标签的名字")

# ⑤ 有话题在用 → 不许删；没人用 → 能真删
switch_to("admin", ADMIN_PASSWORD, "管理员")
_c, _r, _l = post(f"/admin/kinds/{_made['id']}/delete", {"_csrf": admin_token()},
                  200, "删除正在被使用的标签（应被拒）")
assert_true("还有" in flashes(_r) and "停用" in flashes(_r), "有话题在用的标签被拦下并引导去停用",
            flashes(_r)[:120])
assert_true(kind_by_label(LAB_USED) is not None, "被使用的标签确实没被删掉")

_c, _r, _l = post(f"/admin/kinds/{_unused['id']}/delete", {"_csrf": admin_token()},
                  200, "删除没人使用的标签")
assert_true(kind_by_label(LAB_UNUSED) is None, "空标签确实被删掉了")

# ⑥ 内置标签：不许删；「讨论」不许停用（兜底类型）；「任务」可停用但要能启用回来
_builtin = next((r for r in kinds_db() if r["label"] == "任务"), None)
if _builtin:
    _c, _r, _l = post(f"/admin/kinds/{_builtin['id']}/delete", {"_csrf": admin_token()},
                      200, "删除内置标签「任务」（应被拒）")
    assert_true("内置类型" in flashes(_r) and kind_by_label("任务") is not None,
                "内置标签被拦下（代码里有专门流程）", flashes(_r)[:120])
    post(f"/admin/kinds/{_builtin['id']}/toggle", {"_csrf": admin_token()},
         200, "停用内置标签「任务」（应成功，可逆）")
    _t1 = kind_by_label("任务")
    assert_true(_t1 and _t1["is_active"] == 0,
                "「任务」可以停用（有的教研组不做任务，停用后可随时启用回来）", str(_t1))
    post(f"/admin/kinds/{_builtin['id']}/toggle", {"_csrf": admin_token()},
         200, "把「任务」重新启用")
    _t2 = kind_by_label("任务")
    assert_true(_t2 and _t2["is_active"] == 1, "「任务」重新启用后回到标签栏与发布页", str(_t2))
else:
    bad.append(("找不到内置标签「任务」", 0, ""))

_disc = next((r for r in kinds_db() if r["label"] == "讨论"), None)
if _disc:
    _c, _r, _l = post(f"/admin/kinds/{_disc['id']}/toggle", {"_csrf": admin_token()},
                      200, "停用兜底标签「讨论」（应被拒）")
    _disc_now = kind_by_label("讨论")
    assert_true("不能停用" in flashes(_r) and _disc_now and _disc_now["is_active"] == 1,
                "兜底标签「讨论」不能停用", flashes(_r)[:120])
else:
    bad.append(("找不到兜底标签「讨论」", 0, ""))

# ⑦ 改名字 / 换颜色 / 改发布权限
post(f"/admin/kinds/{_made['id']}/update",
     {"_csrf": admin_token(), "label": f"{LAB_USED}改", "color": "orange", "leader_only": "1"},
     200, "改标签名 + 换颜色 + 设为仅组长可发")
_after = kind_by_label(f"{LAB_USED}改")
assert_true(_after is not None and _after["color"] == "orange" and _after["leader_only"] == 1,
            "改名 / 换色 / 发布权限都已生效", str(_after))

# ⑧ 停用：发布页不再出现，但历史话题照常显示
post(f"/admin/kinds/{_made['id']}/toggle", {"_csrf": admin_token()}, 200, "停用该标签")
_st = kind_by_label(f"{LAB_USED}改")
assert_true(_st is not None and _st["is_active"] == 0, "标签已停用（is_active=0）")

switch_to("zhangls", "123456", "张老师")
_, _bp_off = get(f"/b/{B1}", 200)
assert_true(f">{LAB_USED}改</a>" not in _bp_off, "停用后标签栏里不再有这个标签")
_, _np_off = get(f"/b/{B1}/new", 200)
assert_true(f'value="{CODE_USED}"' not in _np_off, "停用后发布页不再提供该选项")
get(f"/b/{B1}?kind={CODE_USED}", 200, "停用后旧筛选链接仍然能打开（历史内容不丢）")
if NEW_TID:
    _, _td2 = get(f"/t/{NEW_TID}", 200)
    assert_true(f"{LAB_USED}改" in _td2, "停用只是不再可选，历史话题的标签照常显示")
    assert_true("t-c-orange" in _td2, "换颜色对历史话题即时生效")

# ⑨ 排序：上移一位后再下移回原位
switch_to("admin", ADMIN_PASSWORD, "管理员")
_order_before = [r["code"] for r in kinds_db()]
_last_row = kinds_db()[-1]
post(f"/admin/kinds/{_last_row['id']}/move", {"_csrf": admin_token(), "dir": "up"},
     200, "把最后一个标签上移一位")
_order_up = [r["code"] for r in kinds_db()]
assert_true(_order_up != _order_before, "上移后顺序变了")
assert_true(_order_up.index(_last_row["code"]) == len(_order_before) - 2,
            "上移后该标签落到倒数第二位", str(_order_up))
post(f"/admin/kinds/{_last_row['id']}/move", {"_csrf": admin_token(), "dir": "down"},
     200, "再下移回原位")
assert_true([r["code"] for r in kinds_db()] == _order_before, "下移后顺序复原")

# ⑩ 收尾：把测试话题删掉，那个标签就变成「没人用」了，于是可以真删
#    （顺带验证上一轮「有内容 → 不许删」不是死结）
if NEW_TID:
    switch_to("zhangls", "123456", "张老师")
    post(f"/t/{NEW_TID}/delete", {"_csrf": csrf_from(get(f"/t/{NEW_TID}", 200)[1])},
         200, "删除冒烟测试话题")
    switch_to("admin", ADMIN_PASSWORD, "管理员")
    post(f"/admin/kinds/{_made['id']}/delete", {"_csrf": admin_token()},
         200, "话题清空后再删该标签（应成功）")
    assert_true(kind_by_label(f"{LAB_USED}改") is None, "话题清空后标签可以真正删除")

assert_true(len(kinds_db()) == len(KINDS), f"标签总数回到 {len(KINDS)} 个（没留下测试标签）",
            str([k["code"] for k in kinds_db()]))
assert_true(len([k for k in kinds_db() if k["is_active"]]) == len(ACTIVE),
            f"启用中的标签数回到 {len(ACTIVE)} 个（后台增删没留下副作用）",
            str([k["code"] for k in kinds_db() if k["is_active"]]))
assert_true([r["code"] for r in kinds_db()] == [k["code"] for k in KINDS],
            "标签顺序与测试前完全一致", str([r["code"] for r in kinds_db()]))

# ---------- 13. 富文本正文 + 评论表情 ----------
# 本次新增：正文能在编辑器里加粗 / 列条目 / 引用 / 放代码，评论能插表情。
# 存储按行记格式：topics.body_format / posts.body_format，老帖一律 'text'（渲染完全不变）。
# 安全底线：富文本入库前做白名单清洗，出库渲染时再洗一遍 —— 把库当成不可信来源。

RT_TITLE = "冒烟测试·富文本（可删）"
RT_XSS_TITLE = "冒烟测试·富文本XSS（可删）"
RT_OLD_TITLE = "冒烟测试·老式纯文本（可删）"
RT_BODY = ("<p>冒烟富文本<strong>粗体</strong>和<em>斜体</em></p>"
           "<ul><li>条目甲</li><li>条目乙</li></ul>"
           "<blockquote>引用一段</blockquote>"
           "<pre><code>print(1)</code></pre>"
           "<p>表情 😀 收尾</p>")
RT_XSS = ('<p>安全文字</p><script>alert("rt")</script>'
          '<img src=x onerror="alert(1)">'
          '<a href="javascript:alert(2)">坏链接</a>'
          '<a href="https://www.qq.com" onclick="alert(3)">好链接</a>'
          '<iframe src="//evil"></iframe>'
          '<div style="position:fixed;top:0">浮层文字</div>')

switch_to("zhangls", "123456", "张老师（富文本与表情）")

# ① 发布页要有编辑器组件与表情面板
_, _nh = get(f"/b/{B1}/new", 200, "发布页（富文本编辑器）")
assert_true("data-rte-bar" in _nh and "data-rte-area" in _nh, "发布页带富文本工具栏与可编辑区")
assert_true('name="body_format"' in _nh, "发布页带正文格式标记字段")
_emoji_n = _nh.count('class="emoji-btn"')
assert_true("data-emoji-pop" in _nh and _emoji_n >= 60, "发布页带表情面板", f"{_emoji_n} 个表情")
assert_true('value="text" data-rte-format' in _nh,
            "没开 JS 时默认按纯文本提交（老路径降级安全）")

# ② 富文本发帖：标签要按 HTML 渲染，不能被转义成文字
post(f"/b/{B1}/new", {"_csrf": csrf_from(_nh), "kind": "discussion", "title": RT_TITLE,
                      "body": RT_BODY, "body_format": "html"}, 200, "发布富文本话题")
_c = _con()
_r1 = _c.execute("SELECT id, body_format FROM topics WHERE title = ?"
                 " ORDER BY id DESC LIMIT 1", (RT_TITLE,)).fetchone()
_c.close()
RT_TID = _r1["id"] if _r1 else None
assert_true(_r1 is not None and _r1["body_format"] == "html",
            "富文本话题按 html 格式入库", str(dict(_r1)) if _r1 else "没建出来")
if RT_TID:
    _, _rd = get(f"/t/{RT_TID}", 200, "富文本话题详情页")
    assert_true("<strong>粗体</strong>" in _rd, "加粗按标签渲染（没被转义）")
    assert_true("<ul>" in _rd and "<li>条目甲</li>" in _rd, "列表按标签渲染")
    assert_true("<blockquote>引用一段</blockquote>" in _rd, "引用块渲染")
    assert_true("<pre><code>print(1)</code></pre>" in _rd, "代码块渲染")
    assert_true("😀" in _rd, "表情字符原样保留")
    assert_true("body-text rich" in _rd, "富文本用 rich 容器（不套 pre-wrap）")
    assert_true("&lt;strong&gt;" not in _rd, "页面上没有转义残留")

# ③ XSS：白名单之外一律不留 —— 脚本连内容一起丢，事件属性与危险协议全清
post(f"/b/{B1}/new", {"_csrf": csrf_from(get(f"/b/{B1}/new", 200)[1]), "kind": "discussion",
                      "title": RT_XSS_TITLE, "body": RT_XSS, "body_format": "html"},
     200, "发布含脚本的富文本（应被清洗）")
_c = _con()
_r2 = _c.execute("SELECT id, body FROM topics WHERE title = ?"
                 " ORDER BY id DESC LIMIT 1", (RT_XSS_TITLE,)).fetchone()
_c.close()
XSS_TID = _r2["id"] if _r2 else None
if _r2 is None:
    bad.append(("含脚本的富文本话题没建出来", 0, ""))
else:
    _sb = _r2["body"]
    assert_true("<script" not in _sb and "</script" not in _sb, "入库时 script 连内容一起被丢掉")
    assert_true("<img" not in _sb and "onerror" not in _sb, "入库时 img / onerror 被丢掉")
    assert_true("javascript:" not in _sb, "入库时 javascript: 协议被拦下")
    assert_true("onclick" not in _sb, "入库时 onclick 这类事件属性被丢掉")
    assert_true("<iframe" not in _sb and "position:fixed" not in _sb,
                "入库时 iframe 与 style 属性被丢掉")
    assert_true("安全文字" in _sb and "坏链接" in _sb, "只脱标签不删内容，正常文字保留")
    assert_true('href="https://www.qq.com"' in _sb and 'rel="noopener noreferrer"' in _sb,
                "正常链接保留并自动补上 rel / target")
    _, _xd = get(f"/t/{XSS_TID}", 200, "含脚本话题的详情页（出库再洗一遍）")
    assert_true("alert(" not in _xd, "页面上看不到任何攻击载荷")
    assert_true("浮层文字" in _xd, "被剥掉 style 的 div 保留了文字")

# ④ 评论：富文本 + 表情（表情插在光标处，存的是字符不是图片）
if RT_TID:
    _, _td0 = get(f"/t/{RT_TID}", 200)
    post(f"/t/{RT_TID}/reply", {"_csrf": csrf_from(_td0), "body": "<p>收到 😄 <b>明白</b></p>",
                                "body_format": "html"}, 200, "发表带表情的富文本评论")
    _, _rp = get(f"/t/{RT_TID}", 200)
    assert_true("收到 😄" in _rp, "评论里的表情正常显示")
    assert_true("<b>明白</b>" in _rp, "评论里的加粗按标签渲染")
    _c = _con()
    _pf = _c.execute("SELECT body_format FROM posts WHERE topic_id = ?"
                     " ORDER BY id DESC LIMIT 1", (RT_TID,)).fetchone()
    _c.close()
    assert_true(_pf is not None and _pf["body_format"] == "html", "评论按 html 格式入库")

# ⑤ 老帖（纯文本）必须一点没变：转义输出 + 靠 pre-wrap 保留换行
_c = _con()
_old = _c.execute("SELECT id, created_at FROM topics WHERE body_format = 'text' AND body != ''"
                  " ORDER BY id LIMIT 1").fetchone()
_uid = _c.execute("SELECT id FROM users WHERE username = 'zhangls'").fetchone()["id"]
_cur = _c.execute(
    "INSERT INTO topics (board_id, author_id, kind, title, body, body_format, is_pinned,"
    " is_featured, status, view_count, reply_count, created_at, updated_at)"
    " VALUES (?,?,'discussion',?,?,'text',0,0,'open',0,0,?,?)",
    (B1, _uid, RT_OLD_TITLE, "第一行 <script>不该执行</script>\n第二行", _old["created_at"],
     _old["created_at"]))
_c.commit()
OLD_TID = _cur.lastrowid
_c.close()
_, _od = get(f"/t/{OLD_TID}", 200, "老式纯文本话题详情页")
assert_true("body-text rich" not in _od, "纯文本话题仍走 body-text（pre-wrap 保换行）")
assert_true("&lt;script&gt;" in _od and "<script>不该执行" not in _od,
            "纯文本里的尖括号照样转义，不会被当成标签")

# ⑥ 搜索摘要不能漏出标签
_, _sh = get("/search?q=" + urllib.parse.quote("明白") + "&scope=posts", 200, "搜索回复内容")
_snips = re.findall(r'<div class="snippet">(.*?)</div>', _sh, re.S)
assert_true(bool(_snips), "搜索页有结果摘要", f"{len(_snips)} 条")
assert_true(all("<b>" not in s and "<p>" not in s for s in _snips),
            "摘要里没有 HTML 标签", str(_snips[:2]))
assert_true(any("明白" in s for s in _snips), "摘要保留了正文文字")

# ⑦ 材料导出：正文要转成纯文本，不能把标签塞进 txt
try:
    with opener.open(f"{BASE}/b/{B1}/export?mode=all", timeout=60) as _r:
        _zdata = _r.read()
    _zf = zipfile.ZipFile(io.BytesIO(_zdata))
    _hit = [n for n in _zf.namelist() if "富文本（可删）" in n and n.endswith(".txt")]
    if _hit:
        _ztxt = _zf.read(_hit[0]).decode("utf-8", "replace")
        assert_true("<p>" not in _ztxt and "<ul>" not in _ztxt, "导出正文里没有 HTML 标签")
        assert_true("粗体" in _ztxt and "条目甲" in _ztxt, "导出正文保留了文字")
        assert_true("· 条目甲" in _ztxt, "导出时列表转成了文字行")
    else:
        bad.append(("导出包里没找到富文本话题的正文", 0, str(_zf.namelist()[:8])))
except Exception as e:  # noqa: BLE001
    bad.append(("富文本话题的材料导出", 0, str(e)))

# ⑧ 收尾：本次造的三个测试话题全部删掉，库里不留痕迹
for _tid in (RT_TID, XSS_TID, OLD_TID):
    if not _tid:
        continue
    post(f"/t/{_tid}/delete", {"_csrf": csrf_from(get(f"/t/{_tid}", 200)[1])},
         200, f"删除富文本冒烟话题 #{_tid}")
_c = _con()
_left = _c.execute("SELECT COUNT(*) FROM topics WHERE title IN (?,?,?)",
                   (RT_TITLE, RT_XSS_TITLE, RT_OLD_TITLE)).fetchone()[0]
_c.close()
assert_true(_left == 0, "富文本自测造的话题已全部清掉", f"还剩 {_left} 个")

# ---------- 14. 列表里显示发布时间（课题组广场 + 课题组页话题列表） ----------
# 需求：「课题列表里增加课题发布的时间显示」。
# 广场卡片要有「建于 YYYY-MM-DD」，课题组页每行话题要有「发布于 <时间>」。
_code, _pz = get("/plaza", 200)
_built = re.findall(r"建于 (\d{4}-\d{2}-\d{2})", _pz)
_n_cards = len(re.findall(r'class="bcard"', _pz))
assert_true(_n_cards > 0, "广场读到了课题组卡片", f"{_n_cards} 张")
assert_true(len(_built) == _n_cards, "每张课题组卡片都显示创建时间（建于 YYYY-MM-DD）",
            f"建于 {len(_built)} 处 vs 卡片 {_n_cards} 张")
assert_true("最近活动 " in _pz, "广场卡片同时标明「最近活动」，两个时间不会混淆")

_code, _bd = get(f"/b/{B1}", 200)
_rows = re.findall(r'<div class="tmeta">(.*?)</div>', _bd, re.S)
_posted = [m for m in _rows if "发布于" in m]
assert_true(len(_rows) > 0, "课题组页读到了话题行", f"{len(_rows)} 行")
assert_true(len(_posted) == len(_rows), "每行话题都显示发布时间",
            f"{len(_posted)}/{len(_rows)} 行")
assert_true(all(re.search(r"发布于 \S", m) for m in _posted),
            "发布时间不是空串（rel_time 真的渲染出了内容）",
            str([re.sub(r"\s+", " ", m).strip()[:70] for m in _posted[:2]]))
# 相对时间（3 天前 / 09-10）之外，悬停还要能看到精确到分钟的时间
_tips = re.findall(r'<span title="\d{4}-\d{2}-\d{2} \d{2}:\d{2}">[^<]*发布于', _bd)
assert_true(len(_tips) == len(_rows), "发布时间带精确到分钟的悬停提示",
            f"{len(_tips)}/{len(_rows)} 行")

# ---------- 15. 正文插图：上传 → 插入 → 认领 → 清洗 → 删帖连图清理 ----------
# 需求：「正文还是不能插入图片」。图片复用附件体系：先传成 draft 附件，
# 正文里只认本站地址 /f/<id>/inline，提交时把附件认领到这条话题/回复上 ——
# 于是图片的可见性、生命周期、导出打包全都跟着正文走，不用另造一张图片表。
GIF_1PX = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
           b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")
_, _img_new = get(f"/b/{B1}/new", 200, "发帖页（插图用例）")
_img_tk = csrf_from(_img_new)


def _pic(fname, content, ctype="image/gif", kind="image"):
    """走插图那条上传路（kind=image 时服务端只收位图）。"""
    return upload_file(B1, fname, content, _img_tk, kind=kind, ctype=ctype)


_st, _up = _pic("冒烟配图.gif", GIF_1PX, "image/gif")
assert_true(_st == 200 and bool(_up.get("ok")), "图片上传成功", str(_up)[:160])
IMG_ID = _up.get("id")
assert_true(bool(IMG_ID), "上传返回附件 id", str(_up)[:160])
assert_true(re.fullmatch(r"/f/\d+/inline", _up.get("preview") or "") is not None,
            "返回的预览地址是站内页内地址", str(_up.get("preview")))
assert_true(_up.get("url") == f"/f/{IMG_ID}", "下载地址与预览地址分开", str(_up.get("url")))

_st2, _up2 = _pic("冒充图片.txt", b"not an image", "text/plain")
assert_true(_st2 == 400 and "图片" in (_up2.get("error") or ""),
            "非图片文件当插图上传被拒", f"{_st2} {_up2}")
_st3, _up3 = _pic("矢量.svg", b"<svg onload=alert(1)></svg>", "image/svg+xml")
assert_true(_st3 == 400, "svg 进不了正文（只收位图）", f"{_st3} {_up3}")
_st4, _up4 = _pic("普通附件.txt", b"hello", "text/plain", kind="")
assert_true(_st4 == 200 and bool(_up4.get("ok")), "普通附件上传不受插图限制影响",
            str(_up4)[:160])

# 发一条图文帖，正文里放站内图片
IMG_TITLE = "冒烟测试：正文插图（可删）"
_, _img_new2 = get(f"/b/{B1}/new", 200)
post(f"/b/{B1}/new", {"_csrf": csrf_from(_img_new2), "kind": "discussion", "title": IMG_TITLE,
                      "body": f'<p>图来了</p><img src="/f/{IMG_ID}/inline" alt="冒烟配图">',
                      "body_format": "html", "attach_ids": str(IMG_ID)},
     200, "发布带图片的话题")
_c = _con()
_it = _c.execute("SELECT id, body_format FROM topics WHERE title = ? ORDER BY id DESC LIMIT 1",
                 (IMG_TITLE,)).fetchone()
_ia = _c.execute("SELECT attachable_type, attachable_id, board_id FROM attachments WHERE id = ?",
                 (IMG_ID,)).fetchone()
_c.close()
IMG_TID = _it["id"] if _it else None
assert_true(IMG_TID is not None, "图文话题已建立")
assert_true(bool(_it) and _it["body_format"] == "html", "图文话题按 html 格式入库")
assert_true(bool(_ia) and _ia["attachable_type"] == "topic" and _ia["attachable_id"] == IMG_TID,
            "图片附件被认领到话题上", str(dict(_ia) if _ia else None))
assert_true(bool(_ia) and _ia["board_id"] == B1, "附件归属课题组正确",
            str(dict(_ia) if _ia else None))

_, _ip = get(f"/t/{IMG_TID}", 200, "图文话题详情页")
assert_true(f'<img src="/f/{IMG_ID}/inline"' in _ip, "详情页渲染出正文图片")
assert_true('alt="冒烟配图"' in _ip, "图片 alt 保留")
assert_true("body-text rich" in _ip, "图文正文走富文本容器")
assert_true("冒烟配图.gif" in _ip, "图片也在附件区（能下载原图）")

# 这里要按**原始字节**取（get() 会按 utf-8 解码，二进制图会失真）
with opener.open(f"{BASE}/f/{IMG_ID}/inline", timeout=20) as _ir:
    _img_raw = _ir.read()
    _img_ctype = _ir.headers.get("Content-Type", "")
assert_true(_img_raw == GIF_1PX, "页内预览返回的就是原图字节", f"{len(_img_raw)} bytes")
assert_true(_img_ctype.startswith("image/gif"), "Content-Type 按图片返回", _img_ctype)
try:
    with noredirect.open(f"{BASE}/f/{IMG_ID}/inline", timeout=10) as _r:
        bad.append(("未登录竟然能直接看正文图片", _r.status, ""))
except urllib.error.HTTPError as _e:
    assert_true(_e.code == 302, "未登录看正文图片被挡回登录页", f"code={_e.code}")

# 清洗：外链图 / onerror / javascript: 一律进不来
IMG_XSS_TITLE = "冒烟测试：图片清洗（可删）"
_, _img_new3 = get(f"/b/{B1}/new", 200)
post(f"/b/{B1}/new",
     {"_csrf": csrf_from(_img_new3), "kind": "discussion", "title": IMG_XSS_TITLE,
      "body": ('<p>正常文字</p><img src="https://evil.com/x.png">'
               f'<img src="/f/{IMG_ID}/inline" onerror="alert(1)">'
               '<img src="javascript:alert(2)">'),
      "body_format": "html"}, 200, "发布含危险图片的话题")
_c = _con()
_ix = _c.execute("SELECT id, body FROM topics WHERE title = ? ORDER BY id DESC LIMIT 1",
                 (IMG_XSS_TITLE,)).fetchone()
_c.close()
IMG_XSS_TID = _ix["id"] if _ix else None
if _ix is None:
    bad.append(("含危险图片的话题没建出来", 0, ""))
else:
    _xb = _ix["body"]
    assert_true("evil.com" not in _xb, "外链图片被清掉")
    assert_true("onerror" not in _xb, "图片上的 onerror 被清掉")
    assert_true("javascript:alert(2)" not in _xb, "javascript: 图源被清掉")
    assert_true(f'<img src="/f/{IMG_ID}/inline">' in _xb, "合法站内图片保留")
    assert_true("正常文字" in _xb, "同一条正文里的文字不受影响")
    _, _xd2 = get(f"/t/{IMG_XSS_TID}", 200, "含危险图片话题的详情页")
    assert_true("evil.com" not in _xd2 and "alert(1)" not in _xd2, "页面上看不到任何载荷")

# 只发一张图、不写字，也要发得出去（is_blank_html 不能把纯图正文判成空）
IMG_ONLY_TITLE = "冒烟测试：只发一张图（可删）"
_, _img_new4 = get(f"/b/{B1}/new", 200)
post(f"/b/{B1}/new", {"_csrf": csrf_from(_img_new4), "kind": "discussion",
                      "title": IMG_ONLY_TITLE,
                      "body": f'<img src="/f/{IMG_ID}/inline">', "body_format": "html"},
     200, "发布只有一张图的话题")
_c = _con()
_io = _c.execute("SELECT id FROM topics WHERE title = ? ORDER BY id DESC LIMIT 1",
                 (IMG_ONLY_TITLE,)).fetchone()
_c.close()
IMG_ONLY_TID = _io["id"] if _io else None
assert_true(IMG_ONLY_TID is not None, "只有一张图的正文没被判成空")

# 收尾：删掉这三个话题，图片附件应随话题一起清掉，磁盘文件也不留
_c = _con()
_img_row = _c.execute("SELECT stored_path FROM attachments WHERE id = ?", (IMG_ID,)).fetchone()
_c.close()
IMG_PATH = (DB_PATH.parent / "uploads" / _img_row["stored_path"]) if _img_row else None
assert_true(bool(IMG_PATH and IMG_PATH.exists()), "图片确实落在 uploads 目录里")
for _tid in (IMG_TID, IMG_XSS_TID, IMG_ONLY_TID):
    if _tid:
        post(f"/t/{_tid}/delete", {"_csrf": csrf_from(get(f"/t/{_tid}", 200)[1])},
             200, f"删除插图冒烟话题 #{_tid}")
_c = _con()
_left_img = _c.execute("SELECT COUNT(*) FROM topics WHERE title IN (?,?,?)",
                       (IMG_TITLE, IMG_XSS_TITLE, IMG_ONLY_TITLE)).fetchone()[0]
_att_left = _c.execute("SELECT COUNT(*) FROM attachments WHERE id = ?", (IMG_ID,)).fetchone()[0]
_c.close()
assert_true(_left_img == 0, "插图自测造的话题已全部清掉", f"还剩 {_left_img} 个")
assert_true(_att_left == 0, "删话题时图片附件记录一起清掉")
assert_true(bool(IMG_PATH and not IMG_PATH.exists()), "图片文件也从 uploads 里删掉了")

print("=" * 66)
print(f"通过 {len(ok)} 项，失败 {len(bad)} 项")
print("=" * 66)
for name, code in ok:
    print(f"  OK   {code:>4}  {name}")
if bad:
    print("-" * 66)
    for name, code, snippet in bad:
        print(f"  FAIL {code:>4}  {name}")
        print(f"         {snippet}")
    sys.exit(1)
