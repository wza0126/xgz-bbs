#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行知教研吧 · 启动入口

本地开发：
    python server.py --port 8009
群晖 NAS：
    python3 server.py --port 8009 --data-dir ~/myproject/0/xgz-bbs/data

优先用 waitress 托管（安装了就用），没有则退回 werkzeug 多线程，
两者都能支撑校内几十人并发。
"""
import argparse
import os
import socket
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="行知教研吧 启动器")
    p.add_argument("--host", default=os.environ.get("BBS_HOST", "0.0.0.0"),
                   help="监听地址，默认 0.0.0.0（校内网可访问）")
    p.add_argument("--port", type=int, default=int(os.environ.get("BBS_PORT", "8009")),
                   help="监听端口，默认 8009")
    p.add_argument("--data-dir", default=os.environ.get("BBS_DATA_DIR"),
                   help="数据目录（数据库 + 附件），默认项目下的 data/")
    p.add_argument("--threads", type=int, default=8, help="工作线程数")
    p.add_argument("--debug", action="store_true", help="开发模式：热重载 + 详细报错")
    return p.parse_args(argv)


def lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips


def main(argv=None):
    args = parse_args(argv)
    if args.data_dir:
        os.environ["BBS_DATA_DIR"] = args.data_dir

    from app import create_app
    from config import DATA_DIR, DB_PATH, UPLOAD_DIR

    app = create_app()

    data_dir_str = str(DATA_DIR)
    print("=" * 62)
    print("  行知教研吧 · 校内教师教研协作平台")
    print("=" * 62)
    print(f"  数据目录 : {data_dir_str}")
    print(f"  数据库   : {DB_PATH}")
    print(f"  附件目录 : {UPLOAD_DIR}")

    if data_dir_str.startswith("\\\\") or data_dir_str.startswith("//"):
        print()
        print("  [!] 警告：数据目录位于网络路径上。")
        print("      SQLite 在网络共享（SMB/NFS）上运行不可靠，可能损坏数据库。")
        print("      请把本程序直接跑在存放数据的这台机器上，不要跨网络写库。")
    print("-" * 62)

    if args.debug:
        app.run(host=args.host, port=args.port, debug=True, threaded=True)
        return

    try:
        from waitress import serve
        print(f"  Web 引擎 : waitress（{args.threads} 线程）")
        for ip in lan_ips():
            print(f"  访问地址 : http://{ip}:{args.port}")
        if args.host in ("127.0.0.1", "localhost"):
            print(f"  本机访问 : http://127.0.0.1:{args.port}")
        print("=" * 62)
        serve(app, host=args.host, port=args.port, threads=args.threads,
              ident="xgz-bbs", clear_untrusted_proxy_headers=True)
    except ImportError:
        from werkzeug.serving import run_simple
        print(f"  Web 引擎 : werkzeug 内置（未装 waitress，建议 pip install waitress）")
        for ip in lan_ips():
            print(f"  访问地址 : http://{ip}:{args.port}")
        print("=" * 62)
        run_simple(args.host, args.port, app, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
