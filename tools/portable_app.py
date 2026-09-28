#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行知教研吧 · 便携演示版启动器

用途：把 exe 和一份 data/ 目录放在同一个文件夹里，拷到任意一台电脑，
      双击 exe 就在本机把网站跑起来；同一局域网（同一个 WiFi / 同一个交换机）
      里的其他人，用控制台里显示的那个 http://<你的IP>:端口 就能打开。

三条硬规矩：
  1. 数据只认 exe 同级的 data/ 目录，**绝不写进 exe 内部**，
     所以 exe 可以随便换地方放，数据跟着文件夹走。
  2. 只在本机起服务，不连 NAS、不改任何远端东西 —— 拿正式库的拷贝演示也不怕。
  3. 端口被占了就自动往后顺延，不会因为「8009 已被使用」而启动失败。

命令行（一般用不上，双击就够）：
    portable_app.exe --port 8080          # 指定端口
    portable_app.exe --data-dir D:\\demo\\data   # 指定数据目录
    portable_app.exe --no-browser         # 不自动开浏览器
"""
from __future__ import annotations

import argparse
import os
import socket
import sqlite3
import sys
import threading
import webbrowser
from pathlib import Path

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8009
PORT_TRIES = 20          # 端口被占用时最多往后试几个
BROWSER_DELAY = 1.2      # 等服务真正起来再开浏览器


# ============================================================
#  路径：exe 在哪、数据放哪、只读资源在哪
# ============================================================

def is_frozen() -> bool:
    """是不是被 PyInstaller 打包成了 exe。"""
    return bool(getattr(sys, "frozen", False))


def runtime_dir() -> Path:
    """「运行目录」：打包后是 exe 所在目录；源码运行时是项目根目录。

    数据目录就挂在这个目录下 —— 所以整个文件夹搬走，数据也跟着走。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """「只读资源目录」：打包后是 PyInstaller 的临时解压目录，
    里面放着模板、CSS、JS、schema.sql。源码运行时就是项目根。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def pick_data_dir(cli_value: str | None) -> Path:
    """数据目录优先级：命令行 > 环境变量 > exe 同级的 data/。"""
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    env = os.environ.get("BBS_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (runtime_dir() / "data").resolve()


# ============================================================
#  控制台输出（Windows 控制台默认是 GBK，直接打特殊符号会崩，
#  所以这里先切 UTF-8，拿不到彩色就老老实实打纯文本）
# ============================================================

class C:
    """极简着色。拿不到 ANSI 能力时全部退化成原文。"""
    _on = False

    @classmethod
    def enable(cls) -> None:
        try:
            if os.name == "nt":
                import ctypes
                k = ctypes.windll.kernel32
                h = k.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
                mode = ctypes.c_uint32()
                if not k.GetConsoleMode(h, ctypes.byref(mode)):
                    return
                cls._on = bool(k.SetConsoleMode(h, mode.value | 0x0004))
            else:
                cls._on = sys.stdout.isatty()
        except Exception:  # noqa: BLE001
            cls._on = False

    @classmethod
    def _w(cls, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if cls._on else text

    @classmethod
    def bold(cls, t):    return cls._w(t, "1")
    @classmethod
    def dim(cls, t):     return cls._w(t, "2")
    @classmethod
    def cyan(cls, t):    return cls._w(t, "36")
    @classmethod
    def green(cls, t):   return cls._w(t, "32")
    @classmethod
    def yellow(cls, t):  return cls._w(t, "33")
    @classmethod
    def red(cls, t):     return cls._w(t, "31")


def setup_console() -> None:
    """让 Windows 控制台能安全打印中文和各种符号。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    C.enable()
    if os.name == "nt":
        try:
            os.system("chcp 65001 > nul")
        except Exception:  # noqa: BLE001
            pass


def out(text: str = "") -> None:
    print(text, flush=True)


# ============================================================
#  网络：挑端口、找本机 IP
# ============================================================

def port_available(host: str, port: int) -> bool:
    """试着占一下这个端口，能占说明可用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def choose_port(host: str, want: int) -> tuple[int, list[int]]:
    """从 want 开始找第一个能用的端口，返回 (端口, 被跳过的端口列表)。"""
    skipped: list[int] = []
    for p in range(want, want + PORT_TRIES + 1):
        if p > 65535:
            break
        if port_available(host, p):
            return p, skipped
        skipped.append(p)
    return 0, skipped


def lan_ips() -> list[str]:
    """本机所有可能被局域网访问到的 IPv4 地址，最可能的排最前面。"""
    ips: list[str] = []
    # ① 出口网卡的地址 —— 通常就是别的电脑要用的那个
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("223.5.5.5", 80))
            primary = s.getsockname()[0]
        finally:
            s.close()
        if primary and not primary.startswith("127."):
            ips.append(primary)
    except OSError:
        pass
    # ② 其它网卡（有线 + 无线 + 手机热点可能同时存在）
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip.startswith("169.254."):
                continue
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


# ============================================================
#  数据目录自检
# ============================================================

def db_stats(db_path: Path) -> str:
    """顺手报一下库里有啥，演示前一眼就知道装的是哪份数据。"""
    try:
        conn = sqlite3.connect(str(db_path), timeout=3)
        try:
            q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
            return (f"{q('SELECT COUNT(*) FROM users')} 用户 / "
                    f"{q('SELECT COUNT(*) FROM topics')} 话题 / "
                    f"{q('SELECT COUNT(*) FROM posts')} 回复 / "
                    f"{q('SELECT COUNT(*) FROM attachments')} 附件")
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return ""


def prepare_data_dir(data_dir: Path) -> bool:
    """确保数据目录存在且可写。返回 False 表示这个目录用不了。

    注意：这里只管「目录能不能写」，不管「里面有没有库」——
    没有库是正常的（首次运行），交给 create_app() 去建。
    """
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        out()
        out(C.red("  [x] 数据目录不可写：") + str(data_dir))
        out(f"      原因：{e}")
        out(C.yellow("      换个地方放 —— 别放在 C:\\Program Files 这类需要管理员权限的目录里，"))
        out(C.yellow("      也别放在只读的 U 盘或光盘上。"))
        return False
    return True


# ============================================================
#  主流程
# ============================================================

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="行知教研吧 便携演示版",
        description="双击即在本机起服务，同一局域网内可访问。")
    p.add_argument("--host", default=os.environ.get("BBS_HOST", DEFAULT_HOST),
                   help=f"监听地址，默认 {DEFAULT_HOST}（局域网可访问）")
    p.add_argument("--port", type=int, default=int(os.environ.get("BBS_PORT", DEFAULT_PORT)),
                   help=f"监听端口，默认 {DEFAULT_PORT}；被占用会自动顺延")
    p.add_argument("--data-dir", default=os.environ.get("BBS_DATA_DIR"),
                   help="数据目录，默认取 exe 同级的 data/")
    p.add_argument("--threads", type=int, default=8, help="工作线程数（默认 8）")
    p.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    return p.parse_args(argv)


def print_banner(data_dir, db_path, port, fresh, stats, skipped, ips, host):
    bar = "=" * 64
    thin = "-" * 64
    out()
    out(C.cyan(bar))
    out("  " + C.bold("行知教研吧 · 便携演示版"))
    out(C.dim("  校内教师教研协作平台"))
    out(C.cyan(bar))
    out("  数据目录 : " + str(data_dir))
    if fresh:
        out("  数据库   : " + C.yellow("（空的，本次首次运行会自动建库）"))
    else:
        out("  数据库   : " + str(db_path))
        if stats:
            out("  库内容   : " + C.green(stats))
    if skipped:
        out("  " + C.yellow(f"端口提示 : {skipped[0]} 已被占用，自动改用 {port}"))
    out(C.cyan(thin))

    if host in ("127.0.0.1", "localhost"):
        out("  本机访问 : " + C.cyan(f"http://127.0.0.1:{port}"))
        out(C.yellow("  [!] 当前只监听本机，别人的电脑打不开。"))
    else:
        out("  本机访问 : " + C.cyan(f"http://127.0.0.1:{port}"))
        if ips:
            out("  " + C.bold("给别人访问（把这一行发给对方）:"))
            for ip in ips:
                out("             " + C.green(f"http://{ip}:{port}"))
        else:
            out(C.yellow("  [!] 没探测到局域网地址，可能这台电脑还没连上 WiFi / 网线。"))
    out(C.cyan(thin))
    out(C.dim("  停止服务 : 在这个窗口按 Ctrl+C") )
    out(C.dim("  提示     : 第一次运行时 Windows 可能弹出防火墙询问，请点「允许访问」。"))
    out(C.cyan(bar))
    out()


def main(argv=None) -> int:
    setup_console()
    args = parse_args(argv)

    runtime = runtime_dir()
    data_dir = pick_data_dir(args.data_dir)

    # ---------- 关键：必须在 import config 之前把数据目录钉死 ----------
    # config.py 一被导入就按 __file__ 算 DATA_DIR，所以时序不能反。
    os.environ["BBS_DATA_DIR"] = str(data_dir)

    bundle = bundle_dir()
    if str(bundle) not in sys.path:
        sys.path.insert(0, str(bundle))

    # 打包时漏了模板的话，早点报错，别等页面 500 了才发现
    tpl = bundle / "app" / "templates"
    if not tpl.is_dir():
        out(C.red(f"  [x] 找不到页面模板目录：{tpl}"))
        out("      这个 exe 打包得不完整，请重新生成后再用。")
        return 2

    # 「进来之前就没有库」＝ 这次是全新启动，等下会自动建库
    fresh_looking = not (data_dir / "bbs.db").exists()
    if not prepare_data_dir(data_dir):
        return 2
    db_path = data_dir / "bbs.db"

    port, skipped = choose_port(args.host, args.port)
    if not port:
        out(C.red(f"  [x] 从 {args.port} 往后试了 {PORT_TRIES} 个端口都被占用了。"))
        out("      关掉几个程序再试，或者用 --port 指定别的端口。")
        return 2

    try:
        from app import create_app
    except Exception as e:  # noqa: BLE001
        out(C.red(f"  [x] 应用启动失败：{e}"))
        import traceback
        traceback.print_exc()
        return 3

    app = create_app()

    # create_app() 已经把库建好/补齐了，这时候读统计才准
    stats = "" if fresh_looking else db_stats(db_path)
    print_banner(data_dir, db_path, port, fresh_looking, stats, skipped,
                 lan_ips(), args.host)

    if fresh_looking:
        out(C.yellow("  这是第一次运行，数据库刚建好："))
        out("      默认管理员账号 " + C.bold("admin") + " / 密码 " + C.bold("admin123"))
        out("      想演示真实数据，就把正式那台的 data/ 整个拷过来，放在 exe 旁边。")
        out()

    if not args.no_browser:
        url = f"http://127.0.0.1:{port}"
        threading.Timer(BROWSER_DELAY, lambda: webbrowser.open(url)).start()

    try:
        from waitress import serve
        out(C.dim(f"  Web 引擎 : waitress（{args.threads} 线程）"))
        out()
        serve(app, host=args.host, port=port, threads=args.threads,
              ident="xgz-bbs-demo", clear_untrusted_proxy_headers=True)
    except ImportError:
        out(C.dim("  Web 引擎 : werkzeug 内置（未装 waitress）"))
        out()
        app.run(host=args.host, port=port, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        out()
        out(C.dim("  服务已停止。窗口可以直接关掉了。"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
