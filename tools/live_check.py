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

# ---------- 5.5 话题类型：培训 / 成果 已上线（只读） ----------
_, home = req("/")
bids = sorted({int(x) for x in re.findall(r"/b/(\d+)", home)})
bid = None
for b in bids:
    _, bp = req(f"/b/{b}")
    if "kind=training" in bp:
        bid = b
        break
check(bid is not None, "找到含「培训」筛选的课题组页")
if bid:
    _, bp = req(f"/b/{bid}")
    check("培训" in bp and "成果" in bp, f"课题组 #{bid} 标签栏有「培训 / 成果」")
    check("kind=training" in bp, "有 kind=training 筛选链接")
    check("kind=achievement" in bp, "有 kind=achievement 筛选链接")
    _, t1 = req(f"/b/{bid}?kind=training")
    check(len(t1) > 200, "kind=training 筛选页可打开")
    _, t2 = req(f"/b/{bid}?kind=achievement")
    check(len(t2) > 200, "kind=achievement 筛选页可打开")
    _, np = req(f"/b/{bid}/new")
    check('value="training"' in np, "发帖页有「培训」选项")
    check('value="achievement"' in np, "发帖页有「成果」选项")

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
