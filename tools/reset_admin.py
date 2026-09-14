# -*- coding: utf-8 -*-
"""
忘记管理员密码时的救援脚本。
用法（在项目根目录）：
    python3 tools/reset_admin.py                      # 把 admin 重置为 admin123
    python3 tools/reset_admin.py --user zhangls --password Abc123  # 指定账号
    python3 tools/reset_admin.py --list               # 只列出现有账号
"""
import argparse
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("BBS_DATA_DIR", str(BASE / "data"))

from app import create_app                      # noqa: E402
from app import db as dbm                       # noqa: E402
from app.auth import hash_password              # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin123")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        if args.list:
            rows = dbm.rows("SELECT username, display_name, role, is_active FROM users"
                            " ORDER BY role, username")
            print(f"{'账号':<16}{'姓名':<12}{'角色':<10}状态")
            for r in rows:
                print(f"{r['username']:<16}{r['display_name']:<12}{r['role']:<10}"
                      f"{'启用' if r['is_active'] else '停用'}")
            return

        u = dbm.row("SELECT * FROM users WHERE username = ?", (args.user,))
        if u is None:
            print(f"没有找到账号 {args.user}，可以先用 --list 看看有哪些账号。")
            sys.exit(1)
        if len(args.password) < 6:
            print("密码至少 6 位。")
            sys.exit(1)
        dbm.execute("UPDATE users SET password_hash = ?, is_active = 1, role = 'admin'"
                    " WHERE id = ?", (hash_password(args.password), u["id"]))
        print(f"已重置：{u['display_name']}（{u['username']}）→ 密码 {args.password}，并已提为管理员、启用。")
        print("请登录后立刻在「个人设置」里改成自己的密码。")


if __name__ == "__main__":
    main()
