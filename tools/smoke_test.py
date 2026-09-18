# -*- coding: utf-8 -*-
"""全站冒烟测试：登录 -> 遍历所有页面 -> 检查状态码。

设计要点：所有 id 都从数据库里现查，绝不写死 /b/1、/t/1。
这样无论演示数据被 --reset 过几次、自增 id 漂到多少，测试都成立。
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
import json as _json


def upload_file(board_id, filename, content, token):
    """按前端的 multipart 约定往 /upload 传一个文件，返回 (状态码, json)。"""
    if not token:
        # 页面没加载出来（服务没起 / 被踢回登录页）时别抛异常，让用例如实报失败
        return 0, {"raw": "没拿到 csrf token，发帖页可能没打开"}
    bd = "----smoke7788boundary"
    buf = []
    for k, v in (("board_id", str(board_id)), ("attachable_type", "draft"), ("attachable_id", "0")):
        buf.append(f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    buf.append(f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
               f'Content-Type: text/plain\r\n\r\n'.encode() + content + b"\r\n")
    buf.append(f"--{bd}--\r\n".encode())
    rq = urllib.request.Request(BASE + "/upload", data=b"".join(buf), method="POST")
    rq.add_header("Content-Type", f"multipart/form-data; boundary={bd}")
    rq.add_header("X-CSRF-Token", token)
    try:
        with opener.open(rq, timeout=30) as r:
            return r.status, _json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", "replace")[:200]}


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
