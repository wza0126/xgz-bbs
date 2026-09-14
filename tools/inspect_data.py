# -*- coding: utf-8 -*-
"""只读盘点线上数据（用于判断能不能安全跑冒烟测试）。"""
import sqlite3

c = sqlite3.connect("data/bbs.db")
c.row_factory = sqlite3.Row
q = lambda s: c.execute(s).fetchone()[0]  # noqa: E731

print("== 总量 ==")
for t in ("users", "boards", "topics", "posts", "tasks", "task_assignees",
          "attachments", "notifications"):
    print(f"  {t:16}{q('SELECT COUNT(*) FROM ' + t)}")

print("== 账号 ==")
for r in c.execute("SELECT username,display_name,role,is_active,last_login_at"
                   " FROM users ORDER BY role,id"):
    print(f"  {r['role']:8}{r['username']:<10}{r['display_name']:<9}"
          f"启用={r['is_active']} 最近登录={r['last_login_at'] or '从未'}")

print("== 课题组 ==")
for r in c.execute("SELECT b.id,b.name,u.username AS owner FROM boards b"
                   " JOIN users u ON u.id=b.owner_id ORDER BY b.id"):
    print(f"  #{r['id']} {r['name'][:36]:<36} 组长={r['owner']}")

print("== 话题 ==")
for r in c.execute("SELECT t.id,t.kind,t.title,u.username FROM topics t"
                   " JOIN users u ON u.id=t.author_id ORDER BY t.id"):
    print(f"  #{r['id']} [{r['kind']:10}] {r['title'][:32]:<32} by {r['username']}")

print("== 各账号内容足迹（删账号时会用到）==")
own = {r["owner_id"] for r in c.execute("SELECT owner_id FROM boards")}
for u in c.execute("SELECT id,username,display_name FROM users ORDER BY id"):
    uid = u["id"]
    n = lambda s: c.execute(s, (uid,)).fetchone()[0]  # noqa: E731
    print(f"  {u['username']:<10} 组长={str(uid in own):<6}"
          f"话题{n('SELECT COUNT(*) FROM topics WHERE author_id=?')} "
          f"回复{n('SELECT COUNT(*) FROM posts WHERE author_id=?')} "
          f"附件{n('SELECT COUNT(*) FROM attachments WHERE owner_id=?')} "
          f"任务{n('SELECT COUNT(*) FROM tasks WHERE created_by=?')} "
          f"审核{n('SELECT COUNT(*) FROM task_assignees WHERE reviewed_by=?')}")
