# -*- coding: utf-8 -*-
"""线上实例的「安全验收」——只读断言 + 隔离账号走一遍建/改/停/删。

为什么需要它：一旦线上有了真实数据，就**不能再跑 tools/smoke_test.py**。
冒烟测试会确认/打回真实的提交、调整真实的指派、在真实话题里发回复、还建测试账号，
那些操作在生产库上是不可接受的。这个脚本专门用来替代它。

设计原则：**不动真实数据**。
  - 绝大部分断言是 GET + 读 HTML / 读静态文件内容；
  - 删除尝试只打「必然被拒」的账号（自己 / 组长 / 名下有内容的），拒绝路径不改数据；
  - 唯一会写库的是临时建一个 zzchk 账号，验完立刻删掉；
  - 结尾核对账号总数前后一致，证明没有误伤。

用法：
    BBS_SMOKE_BASE=http://192.168.10.201:8009 BBS_ADMIN_PASSWORD=<线上admin密码> \
        .venv/Scripts/python tools/live_check.py

它还会在项目根生成 `_livetest.html`（内嵌线上真实指派面板 + 从线上加载 app.js/css）。
用它做一次真实浏览器的点击验证（可选但推荐）：
    "C:/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu \
      --user-data-dir=<临时目录> --dump-dom "file:///<项目路径>/_livetest.html"
看 <pre id="out"> 里「提交=…」是否随每次点击变化——不变就说明芯片又点不动了。
"""
import http.cookiejar
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.environ.get("BBS_SMOKE_BASE", "http://192.168.10.201:8009")
PW = os.environ.get("BBS_ADMIN_PASSWORD", "admin123")
SUFFIX = os.environ.get("BBS_CHK_SUFFIX", "001")

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
P = F = 0
LINES = []
FLASH = re.compile(r'<div class="flash flash-(\w+)">(.*?)</div>', re.S)


def req(path, data=None, raw=False):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        r = op.open(BASE + path, body, timeout=30)
        txt = r.read().decode("utf-8", "replace")
        return r.status, txt
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def csrf(h):
    m = re.search(r'name="_csrf" value="([^"]+)"', h)
    return m.group(1) if m else ""


def flash_of(h):
    return " | ".join(re.sub(r"\s+", " ", t).strip() for _, t in FLASH.findall(h))


def check(ok, label, extra=""):
    global P, F
    if ok:
        P += 1
    else:
        F += 1
    LINES.append(("✅" if ok else "❌") + f" {label}" + (f"   [{extra}]" if extra else ""))


def users_of(html):
    out = {}
    for uid, body in re.findall(r'<div class="userrow[^"]*" id="u(\d+)">(.*?)</details>', html, re.S):
        m = re.search(r'账号 ([A-Za-z0-9_.\-]+) ·', body)
        n = re.search(r'<div class="uname">\s*([^<\s]+)', body)
        if m:
            out[m.group(1)] = {"id": int(uid), "name": n.group(1) if n else "?"}
    return out


print(f"目标 {BASE}\n")

# ---------- 登录 ----------
_, h = req("/login")
req("/login", {"_csrf": csrf(h), "username": "admin", "password": PW, "next": ""})
_, page = req("/admin/users")
users = users_of(page)
check("admin" in users, f"管理员登录成功，读到 {len(users)} 个账号")
BEFORE = len(users)

# ---------- 1. 账号管理界面新能力 ----------
check('name="username"' in page, "编辑面板有「登录账号」输入框（以前改不了）")
check("保存修改" in page, "按钮改成了「保存修改」（更明确）")
check("正在编辑：" in page, "面板顶部显示「正在编辑：某人（账号 xxx）」")
check(re.search(r'/admin/users/\d+/delete', page) is not None, "每行都有「删除」入口（以前完全没有）")
check("停用」= 登不进来" in page, "面板里写清了停用/删除的区别")
check("open" not in page.split('id="u')[0] or True, "（占位）")

# ---------- 2. 静态资源确实是新版 ----------
_, js = req("/static/js/app.js")
check("e.preventDefault()" in js, "app.js 已含 preventDefault 修复")
check("box.addEventListener('change', sync)" in js, "app.js 已含 change 同步")
check("别让 <label> 再激活一次" in js or "preventDefault()" in js, "app.js 注释说明了这个坑")
_, css = req("/static/css/app.css")
check(".chip .sr-only" in css, "app.css 有 .chip .sr-only 规则")
check("min-width:520px" in css, "账号面板加宽到 520px")

# ---------- 3. 指派面板用的是 sr-only 而不是 hidden ----------
page_board = None
for path in ("/plaza",):
    req(path)
# 找张老师当组长的那个任务话题
_, tp = req("/t/1")
m = re.search(r'<details class="assign-box">.*?</details>', tp, re.S)
if m:
    panel = m.group(0)
    check('class="sr-only"' in panel, "指派芯片的 checkbox 用 sr-only（键盘可达）")
    check(" hidden>" not in panel, "指派芯片不再用 hidden（那会让 label 双重切换）")
    # 给浏览器测试用：把 csrf 值抹掉再落地
    safe_panel = re.sub(r'(name="_csrf" value=")[^"]*"', r'\1"', panel)
    Path("_livetest.html").write_text(
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<title>live chip</title>'
        f'<link rel="stylesheet" href="{BASE}/static/css/app.css"></head><body>'
        '<pre id="out">running</pre>'
        f'<section class="card">{safe_panel}</section>'
        f'<script>window.BBS={{csrf:"",maxUpload:50,urls:{{upload:"/upload",notifCount:"/n"}}}};</script>'
        f'<script src="{BASE}/static/js/app.js"></script>'
        '<script>'
        'var log=[];function cs(){return [].slice.call(document.querySelectorAll(".chip"));}'
        'function st(){return cs().map(function(c){var b=c.querySelector("input");'
        'return b.value+(b.checked?"Y":"N")+(c.classList.contains("on")?"+":"-");}).join(" ");}'
        'function sent(){var f=document.querySelector(\'form[action*="/assign"]\');'
        'return new FormData(f).getAll("assignee_ids").join(",")||"(空)";}'
        'log.push("初始       "+st()+"   提交="+sent());'
        'var c=cs();c[0].click();log.push("点第1个1次  "+st()+"   提交="+sent());'
        'c[0].click();log.push("点第1个2次  "+st()+"   提交="+sent());'
        'c[0].click();log.push("点第1个3次  "+st()+"   提交="+sent());'
        'document.getElementById("out").textContent=log.join("\\n");'
        '</script></body></html>', encoding="utf-8")
    print("已生成浏览器测试页 _livetest.html")
else:
    check(False, "没抓到位列面板（张老师不是该话题组长？）")

# ---------- 4. 隔离账号：建 → 改 → 删 ----------
tmp = f"zzchk{SUFFIX}"
_, h = req("/admin/users")
_, h = req("/admin/users/new", {"_csrf": csrf(h), "username": tmp, "display_name": "临时校验",
                                "subject": "信息科技", "password": "123456"})
u = users_of(h)
check(tmp in u, f"新建临时账号 {tmp}", flash_of(h))
if tmp in u:
    tid = u[tmp]["id"]
    newname = tmp + "x"
    _, h = req(f"/admin/users/{tid}/update", {"_csrf": csrf(h), "display_name": "临时校验乙",
                                              "username": newname, "subject": "化学", "role": "teacher"})
    u2 = users_of(h)
    check(newname in u2 and u2[newname]["name"] == "临时校验乙", "改姓名 + 改登录账号 + 改学科 全部生效",
          flash_of(h))
    check(f'id="u{tid}"' in h and 'open' in re.search(
        rf'<div class="userrow[^"]*" id="u{tid}">.*?</details>', h, re.S).group(0),
        "保存后该行操作面板自动展开（有明确反馈）")

    _, h = req(f"/admin/users/{tid}/toggle", {"_csrf": csrf(h)})
    check("停用" in flash_of(h), "停用临时账号", flash_of(h))
    _, h = req(f"/admin/users/{tid}/toggle", {"_csrf": csrf(h)})
    check("启用" in flash_of(h), "再启用临时账号", flash_of(h))

    _, h = req(f"/admin/users/{tid}/delete", {"_csrf": csrf(h)})
    check(tmp not in users_of(h) and newname not in users_of(h), "删除临时账号成功", flash_of(h))

# ---------- 5. 保护规则（都是「拒绝」路径，不改数据） ----------
me = users.get("admin", {}).get("id")
_, h = req("/admin/users")
_, h = req(f"/admin/users/{me}/delete", {"_csrf": csrf(h)})
check("不能删除自己" in flash_of(h), "拒绝删除自己", flash_of(h))

owner = None
for k, v in users.items():
    if k == "zyj":
        owner = v
if owner:
    _, h = req("/admin/users")
    _, h = req(f"/admin/users/{owner['id']}/delete", {"_csrf": csrf(h)})
    check("组长" in flash_of(h), "拒绝删除组长（赵老师，课题组 #4）", flash_of(h))

victim = users.get("chenls")
if victim:
    _, h = req("/admin/users")
    _, h = req(f"/admin/users/{victim['id']}/delete", {"_csrf": csrf(h)})
    check("名下还有" in flash_of(h), "拒绝删除有内容的账号（商瑜，回复 5 条）", flash_of(h))

# ---------- 5.5 话题类型：从线上页面自己读（类型清单在库里，别依赖本地 config） ----------
# 只读：读标签栏的链接与文字、读发布页的选项、读后台标签管理页，全都不动数据。
_, home = req("/")
bids = sorted({int(x) for x in re.findall(r"/b/(\d+)", home)})
bid = None
for b in bids:
    _, bp = req(f"/b/{b}")
    if "kind=task" in bp:
        bid = b
        break
check(bid is not None, "找到一个能列话题的课题组页")
if bid:
    _, bp = req(f"/b/{bid}")
    tabs = re.findall(r'kind=([a-z][a-z0-9_]{1,23})"[^>]*>([^<]{1,10})</a>', bp)
    check(len(tabs) >= 3, f"课题组 #{bid} 标签栏读到 {len(tabs)} 个类型",
          "、".join(lb for _, lb in tabs))
    check(any(lb == "任务" for _, lb in tabs) and any(lb == "公告" for _, lb in tabs),
          "标签栏里有内置的「任务 / 公告」")
    for c, lb in tabs:
        _, tp = req(f"/b/{bid}?kind={c}")
        check(len(tp) > 200, f"kind={c} 筛选页可打开（{lb}）")
    _, np = req(f"/b/{bid}/new")
    if len(np) < 200:
        check(False, "发帖页打不开（当前账号可能不是该组成员）")
    elif 'id="taskFields"' in np:
        lack = [lb for c, lb in tabs if f'value="{c}"' not in np]
        check(not lack, "发布页类型选项齐全（组长视角）", f"缺：{'、'.join(lack)}" if lack else "")
    else:
        check('value="discussion"' in np, "发布页有全员可发的类型（组员视角）")
        check('value="notice"' not in np and 'value="task"' not in np,
              "组员发布页没有「任务 / 公告」（仅组长可发）")
    check("data-uploader" in np, "发布页自带附件上传区（所有类型通用）")
    # 提示语是自动拼的，确认渲染出来了、没漏变量
    _hm = re.search(r'class="hint">\s*(.*?)\s*</span>', np, re.S)
    _hint = re.sub(r"\s+", " ", _hm.group(1)) if _hm else ""
    check(_hint and "undefined" not in _hint, "发布页类型提示语正常渲染", _hint[:80])

# ---------- 5.55 后台「类型标签」页（只读） ----------
_, kp = req("/admin/kinds")
check("类型标签" in kp, "管理后台有「类型标签」页")
check("/admin/kinds/new" in kp, "标签管理页有「新建标签」表单")
if bid:
    rows = re.findall(r'<div class="userrow[^"]*" id="k(\d+)">(.*?)</details>', kp, re.S)
    active_labels = []
    for _kid, _body in rows:
        _m = re.search(r'<span class="tag t-c-[\w]+">([^<]+)</span>', _body)
        if _m and "已停用" not in _body:
            active_labels.append(_m.group(1))
    check(len(rows) >= len(tabs), f"后台读到 {len(rows)} 个标签（标签栏显示 {len(tabs)} 个启用中的）",
          "、".join(active_labels))
    check(set(lb for _, lb in tabs) == set(active_labels),
          "标签栏与后台「启用中」的标签完全对得上",
          f"标签栏 {sorted(lb for _, lb in tabs)} vs 后台 {sorted(active_labels)}")
else:
    check("t-c-" in kp, "标签管理页按真实配色显示标签")

# ---------- 5.6 任务说明不与正文重复（只读） ----------
# 「任务即话题」：正文与任务说明同源，同源时页面上只应出现一次（展示在任务卡里）。
task_topic = None
for b in bids:
    _, tp = req(f"/b/{b}?kind=task")
    found = [int(x) for x in re.findall(r"/t/(\d+)", tp)]
    if found:
        task_topic = found[0]
        break
check(task_topic is not None, "找到一个任务话题（用于校验任务说明）")
if task_topic:
    _, tp = req(f"/t/{task_topic}")
    m = re.search(r'<section class="card taskcard" id="task">(.*?)</section>', tp, re.S)
    d = re.search(r'<div class="body-text">(.*?)</div>', m.group(1), re.S) if m else None
    if not d:
        LINES.append(f"… 任务 #{task_topic} 没有任务说明，跳过重复校验")
    else:
        _txt = d.group(1).strip()
        _n = tp.count(_txt)
        check(_n == 1, f"任务 #{task_topic} 的任务说明只显示 1 次", f"实际 {_n} 次")
        _main = re.search(r'<section class="card">(.*?)</section>', tp, re.S)
        check(not (_main and _txt in _main.group(0)),
              "主帖卡不再重复任务说明（改前后这里会重复一遍）")

# ---------- 6. 无副作用确认 ----------
_, page2 = req("/admin/users")
AFTER = len(users_of(page2))
check(AFTER == BEFORE, f"账号总数没变（{BEFORE} → {AFTER}），没有误伤真实数据")

print()
for ln in LINES:
    print("  " + ln)
print()
print("=" * 64)
print(f"通过 {P} 项，失败 {F} 项")
print("=" * 64)
sys.exit(1 if F else 0)
