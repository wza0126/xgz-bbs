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
    (f"/b/{B1}?kind=training", 200, "课题组 1·培训筛选"),
    (f"/b/{B1}?kind=achievement", 200, "课题组 1·成果筛选"),
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
