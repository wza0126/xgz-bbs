#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行知教研吧 · 一键部署（Windows 端）

把本机源码增量同步到群晖 NAS，然后备份数据库 -> 清缓存 -> 重启 -> 自检。
双击 exe 走图形界面；加 --cli 走命令行（便于脚本化与排障）。

为什么要「增量 + md5 回验」而不是整目录覆盖：
  1) 每次只传真正变了的文件，通常 1~3 个，几秒传完；
  2) 每个文件上传后立刻在远端 md5sum 回比，对不上就报错，绝不静默半成品；
  3) data/ 是硬禁区 —— 线上有老师们的真实数据，任何时候都不能被本地覆盖。

为什么走 base64 + exec_command 而不是 SFTP/scp：
  群晖 DSM 默认关掉了 SFTP 子系统，本机也没有 sshpass；
  base64 走 exec_command 既稳又能保住原始字节（.sh 一旦变 CRLF，NAS 上跑不起来）。

密码只从三处读，优先级：命令行/环境变量 > 配置文件 > 界面输入。
配置文件（exe 同目录 deploy.ini）里是 base64 **混淆**存储，不是加密 —— 方便但要自知。
"""
from __future__ import annotations

import argparse
import base64
import configparser
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import paramiko
except ImportError:  # 只在 NAS 上会缺，Windows 端必有
    paramiko = None

APP_TITLE = "行知教研吧 · 一键部署"
APP_VER = "1.0"

# ---------- 连接默认值 ----------
D_HOST = "192.168.10.201"
D_SSH_PORT = 22
D_USER = "wza0126"
D_ROOT = "/volume2/homes/wza0126/myproject"      # NAS 上的部署根（放 venv / 日志 / *.sh）
D_SUBDIR = "0/xgz-bbs"                            # 程序本体相对 D_ROOT 的位置
D_SITE_PORT = 8009
D_KEEP_BACKUPS = 30

SNAPSHOT_DIRNAME = "deploy_snapshot"              # 打包进 exe 的源码快照目录名
INI_NAME = "deploy.ini"

# ---------- 同步范围 ----------
# 顶层单文件（相对项目根 -> 同名）
TOP_FILES = ("server.py", "config.py", "requirements.txt", "README.md")
# 整目录同步
SYNC_DIRS = ("app", "tools", "docs")
# deploy/*.sh 特殊：远端放在部署根（~/myproject/），不是程序目录里
SH_DIR = "deploy"

EXCLUDE_DIR_PARTS = {
    "__pycache__", "data", "venv", ".venv", ".git", ".workbuddy",
    ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
}
EXCLUDE_FILE_NAMES = {".DS_Store", "Thumbs.db", INI_NAME}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".pyd"}
# Windows 专用脚本（NAS 上没有 paramiko），不必传上去
EXCLUDE_REL = {"tools/deploy_nas.py", "tools/build_deployer.py"}

# data/ 是绝对禁区，任何情况下都不许出现在上传清单里
FORBIDDEN_PREFIX = ("data/", "data", ".workbuddy/", ".workbuddy")

PROJECT_MARKERS = ("server.py", "config.py", "app/__init__.py")

# 只有这些改了才值得重启：.py 要重新 import，.sql 里有建表/加列要在启动时跑。
# 模板（TEMPLATES_AUTO_RELOAD 已开）和 css/js（静态文件直读磁盘）改了都不用重启 ——
# 少重启一次，就少打断一次正在用平台的老师。
RESTART_SUFFIX = {".py", ".sql"}
RESTART_NAMES = {"requirements.txt"}


# ============================================================
#  小工具
# ============================================================

def shq(s: str) -> str:
    """给远端 shell 用的单引号转义。"""
    return "'" + str(s).replace("'", "'\\''") + "'"


def md5_bytes(raw: bytes) -> str:
    return hashlib.md5(raw).hexdigest()


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.2f} MB"


def setup_console_utf8():
    """Windows 控制台默认是 GBK：中文会乱码，emoji 直接抛 UnicodeEncodeError。

    切成 UTF-8（现代 Windows 控制台能正常显示），同时给 stdout 兜底 errors=replace ——
    就算切不动（比如输出被重定向、或者老 conhost），也只是显示成问号，绝不崩。
    """
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:  # noqa: BLE001
            pass
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


EMOJI_TAGS = {"info": "   ", "ok": " ✅", "warn": " ⚠", "err": " ❌", "dim": "   ",
              "head": "###"}
ASCII_TAGS = {"info": "   ", "ok": " OK", "warn": "  !", "err": "ERR", "dim": "   ",
              "head": "###"}


def pick_tags() -> dict:
    """控制台能编码 emoji 就用 emoji，不能就退成 ASCII —— 保证任何终端都不崩。"""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "✅⚠❌".encode(enc)
        return EMOJI_TAGS
    except Exception:  # noqa: BLE001
        return ASCII_TAGS


def looks_like_project(p: Path) -> bool:
    try:
        return all((p / m).exists() for m in PROJECT_MARKERS)
    except OSError:
        return False


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """exe 所在目录（源码模式下是项目根）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def ini_path() -> Path:
    return app_dir() / INI_NAME


def enc(text: str) -> str:
    try:
        return base64.b64encode(str(text).encode("utf-8")).decode("ascii")
    except Exception:  # noqa: BLE001
        return ""


def dec(text: str) -> str:
    try:
        return base64.b64decode(str(text).encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


def read_build_info() -> dict:
    """打包时写进快照的构建信息（源码模式下现算）。"""
    for base in (snapshot_dir(), app_dir() / SNAPSHOT_DIRNAME):
        f = base / "_build_info.json"
        if f.is_file():
            try:
                return json.loads(f.read_text("utf-8"))
            except Exception:  # noqa: BLE001
                pass
    return {"stamp": "源码模式", "git": local_git_sha(app_dir()), "files": 0}


def snapshot_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", app_dir())) / SNAPSHOT_DIRNAME
    return app_dir() / SNAPSHOT_DIRNAME


def local_git_sha(root: Path) -> str:
    """不依赖 git 命令，直接读 .git 里的文件（打包机 / 别的电脑上都能用）。"""
    try:
        head = (root / ".git" / "HEAD").read_text("utf-8").strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            f = root / ".git" / ref
            if f.is_file():
                return f.read_text("utf-8").strip()[:10]
            packed = root / ".git" / "packed-refs"
            if packed.is_file():
                for line in packed.read_text("utf-8").splitlines():
                    if line.endswith(" " + ref):
                        return line.split()[0][:10]
            return ""
        return head[:10]
    except Exception:  # noqa: BLE001
        return ""


def resolve_source(explicit: str = "") -> tuple:
    """定位要部署的源码，返回 (路径, 来源说明)。"""
    if explicit:
        p = Path(explicit).expanduser()
        return (p, "手动指定") if looks_like_project(p) else (p, "手动指定（未识别为项目）")

    here = app_dir()
    if not is_frozen():
        return here, "本地项目"

    # exe 旁边 / 上一级 / 下一级有完整项目就用它（这样改了代码直接双击即生效）
    for cand in (here, here.parent, here / "xgz-bbs"):
        if looks_like_project(cand):
            return cand, "本地项目"

    snap = snapshot_dir()
    if snap.is_dir() and looks_like_project(snap):
        return snap, "内置快照"
    return snap, "内置快照（缺失）"


# ============================================================
#  配置读写
# ============================================================

DEFAULT_CFG = {
    "host": D_HOST,
    "ssh_port": D_SSH_PORT,
    "user": D_USER,
    "password": "",
    "remember": True,
    "remote_root": D_ROOT,
    "site_port": D_SITE_PORT,
    "src_dir": "",
    "skip_backup": False,
    "skip_verify": False,
}


def load_ini() -> dict:
    cfg = dict(DEFAULT_CFG)
    p = ini_path()
    if not p.is_file():
        return cfg
    cp = configparser.ConfigParser()
    try:
        cp.read(p, encoding="utf-8")
    except Exception:  # noqa: BLE001
        return cfg
    if not cp.has_section("nas"):
        return cfg
    s = cp["nas"]
    for k in ("host", "user", "remote_root", "src_dir"):
        if s.get(k):
            cfg[k] = s.get(k).strip()
    for k in ("ssh_port", "site_port"):
        if s.get(k, "").strip().isdigit():
            cfg[k] = int(s.get(k).strip())
    for k in ("remember", "skip_backup", "skip_verify"):
        if s.get(k, "").strip():
            cfg[k] = s.get(k).strip().lower() in ("1", "true", "yes", "on")
    if s.get("password_b64"):
        cfg["password"] = dec(s.get("password_b64").strip())
    return cfg


def save_ini(cfg: dict) -> str:
    cp = configparser.ConfigParser()
    cp["nas"] = {
        "host": str(cfg.get("host", "")),
        "ssh_port": str(cfg.get("ssh_port", 22)),
        "user": str(cfg.get("user", "")),
        "remote_root": str(cfg.get("remote_root", "")),
        "site_port": str(cfg.get("site_port", 8009)),
        "src_dir": str(cfg.get("src_dir", "")),
        "remember": "1" if cfg.get("remember") else "0",
        "skip_backup": "1" if cfg.get("skip_backup") else "0",
        "skip_verify": "1" if cfg.get("skip_verify") else "0",
    }
    if cfg.get("remember") and cfg.get("password"):
        cp["nas"]["password_b64"] = enc(cfg["password"])
    cp["nas"]["_note"] = "password_b64 只是 base64 混淆、不是加密；不想留就取消勾选「记住」。"
    p = ini_path()
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        cp.write(fh)
    return str(p)


# ============================================================
#  部署器
# ============================================================

class DeployError(Exception):
    pass


class Item:
    """一个待同步的文件。"""

    __slots__ = ("rel", "local", "base", "rpath", "size")

    def __init__(self, rel: str, local: Path, base: str, rpath: str):
        self.rel = rel
        self.local = local
        self.base = base
        self.rpath = rpath
        self.size = local.stat().st_size

    def remote_path(self) -> str:
        return self.base.rstrip("/") + "/" + self.rpath


class Deployer:
    def __init__(self, cfg: dict, on_log=None, on_progress=None, on_done=None):
        self.cfg = cfg
        self.on_log = on_log or (lambda lv, msg: None)
        self.on_progress = on_progress or (lambda pct, label: None)
        self.on_done = on_done or (lambda ok, summary: None)

        self.cli = None
        self.root = str(cfg.get("remote_root") or D_ROOT).rstrip("/")
        self.rdir = self.root + "/" + D_SUBDIR
        self.venv_py = self.root + "/venv/bin/python3"
        self.src, self.src_from = resolve_source(str(cfg.get("src_dir") or ""))
        self.plan: list[Item] = []
        self.stats = {
            "uploaded": [], "skipped": 0, "bytes": 0, "deleted_cache": True,
            "backup": "", "pid": "", "restart_sec": 0, "verify": [], "src": str(self.src),
            "src_from": self.src_from, "first_deploy": False,
        }

    # ---------- 日志 ----------
    def log(self, msg: str, level: str = "info"):
        self.on_log(level, msg)

    def prog(self, pct: int, label: str = ""):
        self.on_progress(max(0, min(100, int(pct))), label)

    # ---------- 远端执行 ----------
    def run(self, cmd: str, timeout: int = 300, quiet: bool = False) -> tuple:
        if self.cli is None:
            raise DeployError("尚未连接")
        _in, out, err = self.cli.exec_command(cmd, timeout=timeout, get_pty=False)
        o = out.read().decode("utf-8", "replace")
        e = err.read().decode("utf-8", "replace")
        rc = out.channel.recv_exit_status()
        if not quiet:
            for line in o.splitlines():
                if line.strip():
                    self.log("   " + line.rstrip(), "dim")
            for line in e.splitlines():
                if line.strip():
                    self.log("   " + line.rstrip(), "warn")
        return rc, o, e

    def connect(self):
        if paramiko is None:
            raise DeployError("缺少 paramiko，无法建立 SSH 连接")
        host = str(self.cfg.get("host") or D_HOST)
        user = str(self.cfg.get("user") or D_USER)
        pw = str(self.cfg.get("password") or "")
        port = int(self.cfg.get("ssh_port") or 22)
        if not pw:
            raise DeployError("没有密码：请在界面里填 SSH 密码")

        self.log(f"连接 {user}@{host}:{port} ...")
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            cli.connect(host, port=port, username=user, password=pw,
                        timeout=15, banner_timeout=30, auth_timeout=30,
                        look_for_keys=False, allow_agent=False)
        except paramiko.AuthenticationException:
            raise DeployError("SSH 用户名或密码不对") from None
        except Exception as e:  # noqa: BLE001
            raise DeployError(f"连不上 NAS：{type(e).__name__}: {e}") from None
        self.cli = cli
        self.log("已连上 NAS", "ok")

    def close(self):
        if self.cli is not None:
            try:
                self.cli.close()
            except Exception:  # noqa: BLE001
                pass
            self.cli = None

    # ---------- 上传一个文件（base64 保字节 + md5 回验）----------
    def put(self, raw: bytes, item: Item) -> bool:
        remote = item.remote_path()
        cmd = ("mkdir -p %s && base64 -d > %s && md5sum %s"
               % (shq(os.path.dirname(remote)), shq(remote), shq(remote)))
        _in, out, err = self.cli.exec_command(cmd, timeout=180)
        _in.write(base64.b64encode(raw).decode("ascii") + "\n")
        _in.channel.shutdown_write()
        o = out.read().decode("utf-8", "replace").strip()
        e = err.read().decode("utf-8", "replace").strip()
        got = o.split()[0] if o else ""
        want = md5_bytes(raw)
        if got != want:
            raise DeployError(f"上传校验失败：{item.rel} 本地 {want[:8]} / 远端 {got[:8] or '空'}"
                              + (f" ({e})" if e else ""))
        return True

    # ---------- 远端已有文件的 md5 ----------
    def remote_md5_map(self, base: str, rpaths: list) -> dict:
        if not rpaths:
            return {}
        script = ("cd %s && while IFS= read -r f; do "
                  "if [ -f \"$f\" ]; then md5sum \"$f\"; else echo \"MISSING  $f\"; fi; "
                  "done <<'XGZEOF'\n%s\nXGZEOF" % (shq(base), "\n".join(rpaths)))
        rc, out, _err = self.run(script, timeout=120, quiet=True)
        res: dict = {}
        for line in out.splitlines():
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("MISSING"):
                parts = line.split(None, 1)
                if len(parts) == 2:
                    res[parts[1].strip()] = ""
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                res[parts[1].strip()] = parts[0].strip()
        return res

    # ---------- 收集本地文件 ----------
    def collect(self) -> list:
        src = self.src
        if not looks_like_project(src):
            raise DeployError(f"源码目录不像本项目（缺 server.py / config.py / app/）：{src}")
        items: list[Item] = []

        def add(rel: str, path: Path, base: str, rpath: str):
            low = rel.replace("\\", "/")
            for bad in FORBIDDEN_PREFIX:
                if low == bad.rstrip("/") or low.startswith(bad):
                    raise DeployError(f"安全检查未通过：清单里出现了禁区路径 {rel}")
            items.append(Item(rel, path, base, rpath))

        for name in TOP_FILES:
            p = src / name
            if p.is_file():
                add(name, p, self.rdir, name)

        for sub in SYNC_DIRS:
            d = src / sub
            if not d.is_dir():
                continue
            for p in sorted(d.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(src).as_posix()
                parts = Path(rel).parts[:-1]
                if any(x in EXCLUDE_DIR_PARTS for x in parts):
                    continue
                if p.name in EXCLUDE_FILE_NAMES or p.suffix.lower() in EXCLUDE_SUFFIX:
                    continue
                if rel in EXCLUDE_REL:
                    continue
                add(rel, p, self.rdir, rel)

        sh_dir = src / SH_DIR
        if sh_dir.is_dir():
            for p in sorted(sh_dir.glob("*.sh")):
                add(f"{SH_DIR}/{p.name}", p, self.root, p.name)

        if not items:
            raise DeployError(f"没有收集到任何文件，源码目录可能不对：{src}")
        return items

    # ========== 各步骤 ==========
    def step_preflight(self):
        self.prog(6, "预检")
        rc, out, _ = self.run("hostname; uname -m; whoami; date '+%Y-%m-%d %H:%M:%S'",
                              timeout=40, quiet=True)
        head = " | ".join(x.strip() for x in out.splitlines() if x.strip())
        self.log(f"NAS：{head}", "dim")

        rc, out, _ = self.run(f"[ -f {shq(self.rdir)}/server.py ] && echo YES || echo NO",
                              timeout=30, quiet=True)
        exists = "YES" in out
        self.stats["first_deploy"] = not exists
        if not exists:
            self.log(f"线上还没有程序目录 {self.rdir} —— 本次按【首次部署】走", "warn")
        else:
            rc, out, _ = self.run(f"[ -x {shq(self.venv_py)} ] && echo YES || echo NO",
                                  timeout=30, quiet=True)
            if "YES" not in out:
                raise DeployError(f"找不到虚拟环境 {self.venv_py}，请先在 NAS 上跑 setup-bbs.sh")
            self.log("虚拟环境就绪", "ok")

        pid = self.remote_pid()
        self.log(f"当前服务进程：{pid or '未运行'}", "dim")

    def remote_pid(self) -> str:
        port = int(self.cfg.get("site_port") or D_SITE_PORT)
        rc, out, _ = self.run(
            "netstat -tlnp 2>/dev/null | awk -v p=':%d' 'index($4,p)>0 {print $NF}' "
            "| sed 's#/.*##' | grep -E '^[0-9]+$' | sort -u | tr '\\n' ' '" % port,
            timeout=30, quiet=True)
        return out.strip()

    def step_backup(self) -> str:
        if self.stats["first_deploy"]:
            self.log("首次部署，跳过备份（线上还没有库）", "dim")
            return ""
        self.prog(15, "备份数据库")
        cmd = (f"cd {shq(self.rdir)} && {shq(self.venv_py)} tools/backup_db.py "
               f"--keep {int(self.cfg.get('keep_backups') or D_KEEP_BACKUPS)}")
        rc, out, _ = self.run(cmd, timeout=180)
        line = [x for x in out.splitlines() if "已备份" in x]
        if rc != 0 or not line:
            raise DeployError("数据库备份失败，已中止（线上数据必须先有备份）")
        self.stats["backup"] = line[0].split("已备份")[-1].strip().strip("→").strip()
        return self.stats["backup"]

    def step_diff(self) -> list:
        self.prog(25, "比对差异")
        self.plan = self.collect()
        self.log(f"源码：{self.src}（{self.src_from}），共 {len(self.plan)} 个文件")

        by_base: dict = {}
        for it in self.plan:
            by_base.setdefault(it.base, []).append(it)
        todo: list[Item] = []
        for base, group in by_base.items():
            remote = self.remote_md5_map(base, [g.rpath for g in group])
            for it in group:
                rmd5 = remote.get(it.rpath, "")
                if not rmd5:
                    todo.append(it)
                    self.log(f"  + 新增 {it.rel}", "dim")
                    continue
                if rmd5 != md5_file(it.local):
                    todo.append(it)
                    self.log(f"  ~ 变更 {it.rel}", "dim")
                else:
                    self.stats["skipped"] += 1
        self.log(f"需要上传 {len(todo)} 个，跳过 {self.stats['skipped']} 个未变化",
                 "ok" if todo else "dim")
        return todo

    def step_upload(self, todo: list):
        self.prog(35, "上传")
        if not todo:
            self.log("没有文件需要上传（线上已是最新）", "dim")
            return
        total = len(todo)
        for i, it in enumerate(todo, 1):
            raw = it.local.read_bytes()
            self.put(raw, it)
            self.stats["uploaded"].append(it.rel)
            self.stats["bytes"] += len(raw)
            self.log(f"  ✓ {it.rel}  {human_size(len(raw))}", "ok")
            self.prog(35 + int(33 * i / total), f"上传 {i}/{total}")
        self.log(f"上传完成：{total} 个文件，共 {human_size(self.stats['bytes'])}", "ok")

    def step_clear_cache(self):
        self.prog(70, "清理缓存")
        rc, out, _ = self.run(
            f"find {shq(self.rdir)} -name '__pycache__' -type d -exec rm -rf {{}} + 2>/dev/null; "
            "echo CLEARED", timeout=120, quiet=True)
        self.stats["deleted_cache"] = "CLEARED" in out
        if self.stats["deleted_cache"]:
            self.log("已清 __pycache__（避免加载到旧的 .pyc）", "ok")
        else:
            self.log("清缓存没回显，继续", "warn")

    def step_restart(self):
        self.prog(75, "重启服务")
        if self.stats["first_deploy"]:
            self.log("首次部署：跑 setup-bbs.sh（建库 + 起服务）", "warn")
            rc, out, _ = self.run(f"bash {shq(self.root)}/setup-bbs.sh", timeout=900)
            if rc != 0:
                raise DeployError("setup-bbs.sh 返回非 0，请看上面的输出")
            m = re.search(r"已恢复[^0-9]*(\d+)s", out)
            self.stats["restart_sec"] = int(m.group(1)) if m else 0
            self.stats["pid"] = self.remote_pid()
            self.log("首次部署完成", "ok")
            return

        t0 = time.time()
        rc, out, _ = self.run(f"bash {shq(self.root)}/restart-bbs.sh", timeout=300)
        self.stats["restart_sec"] = int(time.time() - t0)
        if rc != 0 or "服务已恢复" not in out:
            tail = self.remote_log_tail(25)
            raise DeployError("重启失败，最近日志：\n" + tail)
        self.stats["pid"] = self.remote_pid()
        self.log(f"重启完成，耗时 {self.stats['restart_sec']}s，新进程 {self.stats['pid'] or '?'}", "ok")

    def remote_log_tail(self, n: int = 25) -> str:
        try:
            rc, out, _ = self.run(f"tail -n {n} {shq(self.root)}/logs/bbs.log",
                                  timeout=60, quiet=True)
            return out.strip()
        except Exception:  # noqa: BLE001
            return "(取日志失败)"

    # ---------- 自检 ----------
    def http_get(self, path: str, timeout: int = 12):
        host = str(self.cfg.get("host") or D_HOST)
        port = int(self.cfg.get("site_port") or D_SITE_PORT)
        url = f"http://{host}:{port}{path}"
        req = urllib.request.Request(url, headers={"User-Agent": f"xgz-deploy/{APP_VER}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def step_verify(self):
        self.prog(90, "自检")
        ok_list = []

        def chk(name: str, cond: bool, detail: str = ""):
            self.stats["verify"].append((name, bool(cond), detail))
            if cond:
                ok_list.append(name)
                self.log(f"  ✓ {name}" + (f"（{detail}）" if detail else ""), "ok")
            else:
                self.log(f"  ✗ {name}" + (f"（{detail}）" if detail else ""), "err")
            return bool(cond)

        code, body, _ = self.http_get("/healthz")
        chk("健康检查 /healthz 返回 200", code == 200, f"HTTP {code}")

        code, body, _ = self.http_get("/")
        chk("首页可访问", code in (200, 302), f"HTTP {code}")

        # 静态文件直接从磁盘读，md5 与本地一致 => 文件确实同步到了线上
        for rel, url in (("app/static/css/app.css", "/static/css/app.css"),
                         ("app/static/js/app.js", "/static/js/app.js")):
            lp = self.src / rel
            if not lp.is_file():
                continue
            raised = rel in self.stats["uploaded"]
            code, body, _ = self.http_get(url)
            same = code == 200 and md5_bytes(body) == md5_file(lp)
            if raised:
                chk(f"新上传的 {Path(rel).name} 线上内容与本地一致", same,
                    f"HTTP {code}" + ("" if same else " 内容不同"))
            elif not same:
                self.log(f"  · {Path(rel).name} 线上与本地不同（本次没改它，忽略）", "dim")

        # 页面里能拿到 csrf 说明模板渲染正常，不是错误页
        code, body, _ = self.http_get("/login")
        txt = body.decode("utf-8", "replace")
        chk("登录页渲染正常（含 csrf 令牌）", code == 200 and "csrf" in txt.lower(),
            f"HTTP {code}")

        if not ok_list:
            raise DeployError("自检全部失败，服务可能没起来，请查看下面的日志")
        return self.stats["verify"]

    # ---------- 主流程 ----------
    def deploy(self, dry: bool = False):
        t0 = time.time()
        try:
            self.prog(2, "连接")
            self.connect()
            self.step_preflight()

            if dry:
                self.prog(60, "只比对")
                todo = self.step_diff()
                self.log("", "dim")
                self.log("【演练模式】以下文件会被上传（实际没有传）：", "warn")
                for it in todo:
                    self.log(f"  · {it.rel}  {human_size(it.size)}", "dim")
                self.log(f"合计 {len(todo)} 个文件 / "
                         f"{human_size(sum(x.size for x in todo))}", "warn")
                self.prog(100, "演练结束")
                self.on_done(True, self.summary(0, dry=True))
                return True

            if not self.cfg.get("skip_backup"):
                self.step_backup()
            else:
                self.log("按设置跳过备份", "warn")

            todo = self.step_diff()
            self.step_upload(todo)

            need_restart = self.stats["first_deploy"] or any(
                Path(x).suffix.lower() in RESTART_SUFFIX or Path(x).name in RESTART_NAMES
                for x in self.stats["uploaded"])
            if need_restart:
                self.step_clear_cache()
                self.step_restart()
            elif self.stats["uploaded"]:
                self.prog(88, "无需重启")
                self.log("改动只涉及模板 / 静态资源 / 文档 —— 跳过重启", "warn")
                self.log("  模板已开热更，css/js 是直读磁盘的静态文件，改了都立刻生效，"
                         "不用动正在服务的进程", "dim")
            else:
                self.prog(88, "无需重启")
                self.log("线上已是最新，跳过重启（没有文件变化，不打扰正在用的老师）", "warn")

            if not self.cfg.get("skip_verify"):
                self.step_verify()
            else:
                self.log("按设置跳过自检", "warn")

            self.prog(100, "完成")
            self.on_done(True, self.summary(int(time.time() - t0)))
            return True

        except DeployError as e:
            self.prog(100, "失败")
            self.log("", "dim")
            self.log(str(e), "err")
            self.log("", "dim")
            if not self.stats["first_deploy"]:
                self.log("代码已经传上去了，线上数据没动。修好问题再双击一次就行；", "warn")
                self.log(f"要回退代码：把备份的源码覆盖回去并重启（备份目录 "
                         f"{self.rdir}/data/backups）。", "warn")
            self.on_done(False, self.summary(0, error=str(e)))
            return False
        except Exception as e:  # noqa: BLE001
            self.prog(100, "异常")
            self.log(f"未预料的错误：{type(e).__name__}: {e}", "err")
            self.on_done(False, self.summary(0, error=f"{type(e).__name__}: {e}"))
            return False
        finally:
            self.close()

    def summary(self, elapsed: int, dry: bool = False, error: str = "") -> dict:
        d = dict(self.stats)
        d["elapsed"] = elapsed
        d["dry"] = dry
        d["error"] = error
        d["site"] = f"http://{self.cfg.get('host')}:{self.cfg.get('site_port')}"
        return d


def format_summary(s: dict) -> str:
    lines = []
    if s.get("dry"):
        lines.append("【演练完成】没有改动线上任何东西")
    elif s.get("error"):
        lines.append("【部署失败】" + s["error"].splitlines()[0])
    else:
        lines.append("【部署成功】")
    lines.append(f"  源码来源 : {s.get('src')}（{s.get('src_from')}）")
    if not s.get("dry"):
        lines.append(f"  本次上传 : {len(s.get('uploaded') or [])} 个 / 跳过 {s.get('skipped', 0)} 个")
        if s.get("backup"):
            lines.append(f"  数据库备份: {s['backup']}")
        if s.get("pid"):
            lines.append(f"  服务进程 : {s['pid']}")
        if s.get("elapsed"):
            lines.append(f"  总耗时   : {s['elapsed']}s")
        v = s.get("verify") or []
        if v:
            good = sum(1 for _n, c, _d in v if c)
            lines.append(f"  自检     : {good}/{len(v)} 通过")
    lines.append(f"  线上地址 : {s.get('site')}")
    return "\n".join(lines)


# ============================================================
#  命令行
# ============================================================

def build_cfg_from_args(a) -> dict:
    cfg = load_ini()
    if a.host:
        cfg["host"] = a.host
    if a.user:
        cfg["user"] = a.user
    if a.ssh_port:
        cfg["ssh_port"] = a.ssh_port
    if a.remote_root:
        cfg["remote_root"] = a.remote_root
    if a.site_port:
        cfg["site_port"] = a.site_port
    if a.src:
        cfg["src_dir"] = a.src
    if a.no_backup:
        cfg["skip_backup"] = True
    if a.no_verify:
        cfg["skip_verify"] = True
    pw = a.password or os.environ.get("NAS_PW") or os.environ.get("BBS_NAS_PW")
    if pw:
        cfg["password"] = pw
    return cfg


def run_cli(a) -> int:
    setup_console_utf8()
    cfg = build_cfg_from_args(a)
    from datetime import datetime
    tag = pick_tags()

    def emit(line: str):
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(line.encode(enc, "replace").decode(enc, "replace"), flush=True)

    def on_log(level, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        emit(f"[{ts}]{tag.get(level, '   ')} {msg}")

    last = {"pct": -1}

    def on_progress(pct, label):
        if pct != last["pct"]:
            last["pct"] = pct
            emit(f"        ---- {pct:3d}% {label}")

    box = {"ok": False, "summary": None}

    def on_done(ok, summary):
        box["ok"] = ok
        box["summary"] = summary

    emit("=" * 68)
    emit(f"  {APP_TITLE} v{APP_VER}")
    emit("=" * 68)
    d = Deployer(cfg, on_log, on_progress, on_done)
    d.log(f"源码：{d.src}（{d.src_from}）")
    d.log(f"目标：{d.cfg.get('user')}@{d.cfg.get('host')}:{d.rdir}")
    ok = d.deploy(dry=a.dry)
    emit("=" * 68)
    for line in format_summary(box["summary"] or {}).splitlines():
        emit(line)
    emit("=" * 68)
    return 0 if ok else 1


# ============================================================
#  图形界面
# ============================================================

def run_gui(a) -> int:
    import tkinter as tk
    from tkinter import ttk

    cfg = build_cfg_from_args(a)

    root = tk.Tk()
    root.title(f"{APP_TITLE}  v{APP_VER}")
    root.geometry("880x700")
    root.minsize(820, 620)
    try:
        root.tk.call("tk", "scaling", 1.25)
    except Exception:  # noqa: BLE001
        pass

    FONT = ("Microsoft YaHei UI", 9)
    MONO = ("Consolas", 9)
    style = ttk.Style()
    for name in ("TLabel", "TButton", "TCheckbutton", "TLabelframe.Label", "TEntry"):
        try:
            style.configure(name, font=FONT)
        except Exception:  # noqa: BLE001
            pass

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)

    # ---------- 标题 ----------
    head = ttk.Frame(outer)
    head.pack(fill="x")
    ttk.Label(head, text="行知教研吧 · 一键部署", font=("Microsoft YaHei UI", 14, "bold")).pack(side="left")
    info = read_build_info()
    stamp = info.get("stamp") or ""
    sha = info.get("git") or ""
    tail = "  ".join(x for x in (f"打包于 {stamp}" if stamp else "", f"代码 {sha}" if sha else "") if x)
    ttk.Label(head, text=tail, foreground="#888").pack(side="right")

    src_path, src_from = resolve_source(str(cfg.get("src_dir") or ""))
    sub = ttk.Label(outer, foreground="#666",
                    text=f"源码：{src_path}    （{src_from} —— 改代码后重新双击本程序即可上线）")
    sub.pack(fill="x", pady=(2, 10))

    # ---------- 连接设置 ----------
    box = ttk.LabelFrame(outer, text=" NAS 连接 ", padding=10)
    box.pack(fill="x")
    vars_ = {k: tk.StringVar(value=str(cfg.get(k, ""))) for k in
             ("host", "ssh_port", "user", "remote_root", "site_port", "src_dir")}
    var_pw = tk.StringVar(value=str(cfg.get("password") or ""))
    var_remember = tk.BooleanVar(value=bool(cfg.get("remember")))
    var_nobackup = tk.BooleanVar(value=bool(cfg.get("skip_backup")))
    var_noverify = tk.BooleanVar(value=bool(cfg.get("skip_verify")))

    grid = ttk.Frame(box)
    grid.pack(fill="x")
    rows = [("主机", "host", 12), ("SSH 端口", "ssh_port", 6), ("用户", "user", 12),
            ("部署根目录", "remote_root", 34), ("站点端口", "site_port", 6)]
    for r, (label, key, w) in enumerate(rows):
        ttk.Label(grid, text=label).grid(row=r, column=0, sticky="e", padx=(0, 6), pady=2)
        ttk.Entry(grid, textvariable=vars_[key], width=w, font=FONT).grid(
            row=r, column=1, sticky="w", pady=2)
    ttk.Label(grid, text="SSH 密码").grid(row=2, column=2, sticky="e", padx=(16, 6))
    pw_e = ttk.Entry(grid, textvariable=var_pw, width=20, show="*", font=FONT)
    pw_e.grid(row=2, column=3, sticky="w")
    ttk.Checkbutton(grid, text="记住密码", variable=var_remember).grid(
        row=2, column=4, sticky="w", padx=(6, 0))
    ttk.Label(grid, text="源码目录").grid(row=4, column=2, sticky="e", padx=(16, 6))
    ttk.Entry(grid, textvariable=vars_["src_dir"], width=20, font=FONT).grid(
        row=4, column=3, sticky="w")
    ttk.Label(grid, text="留空=自动", foreground="#999").grid(row=4, column=4, sticky="w", padx=(6, 0))

    opt = ttk.Frame(box)
    opt.pack(fill="x", pady=(8, 0))
    ttk.Checkbutton(opt, text="跳过数据库备份（不推荐）", variable=var_nobackup).pack(side="left")
    ttk.Checkbutton(opt, text="跳过自检", variable=var_noverify).pack(side="left", padx=(16, 0))

    # ---------- 动作 ----------
    act = ttk.Frame(outer)
    act.pack(fill="x", pady=(12, 6))
    btn_go = ttk.Button(act, text="开始部署", width=14)
    btn_go.pack(side="left")
    btn_dry = ttk.Button(act, text="只比对（演练）", width=14)
    btn_dry.pack(side="left", padx=(8, 0))
    btn_save = ttk.Button(act, text="保存设置", width=12)
    btn_save.pack(side="left", padx=(8, 0))
    btn_site = ttk.Button(act, text="打开线上站点", width=14)
    btn_site.pack(side="right")

    bar = ttk.Progressbar(outer, mode="determinate", maximum=100)
    bar.pack(fill="x")
    status = ttk.Label(outer, text="准备就绪", foreground="#555")
    status.pack(fill="x", pady=(4, 8))

    # ---------- 日志 ----------
    logbox = ttk.LabelFrame(outer, text=" 部署日志 ", padding=6)
    logbox.pack(fill="both", expand=True)
    txt = tk.Text(logbox, wrap="word", font=MONO, background="#fcfcfc",
                  foreground="#222", relief="flat", state="disabled", height=18)
    sb = ttk.Scrollbar(logbox, command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    txt.pack(side="left", fill="both", expand=True)
    txt.tag_configure("ok", foreground="#0a7a2f")
    txt.tag_configure("warn", foreground="#b26a00")
    txt.tag_configure("err", foreground="#c0392b")
    txt.tag_configure("dim", foreground="#8a8a8a")
    txt.tag_configure("head", foreground="#1a4d8f")

    state = {"running": False}

    def ui_log(level, msg):
        def _do():
            txt.configure(state="normal")
            txt.insert("end", msg + "\n", level)
            txt.see("end")
            txt.configure(state="disabled")
        root.after(0, _do)

    def ui_prog(pct, label):
        def _do():
            bar.configure(value=pct)
            if label:
                status.configure(text=f"{label} · {pct}%")
        root.after(0, _do)

    def collect_cfg() -> dict:
        c = dict(cfg)
        c["host"] = vars_["host"].get().strip()
        c["user"] = vars_["user"].get().strip()
        c["remote_root"] = vars_["remote_root"].get().strip()
        c["src_dir"] = vars_["src_dir"].get().strip()
        for k in ("ssh_port", "site_port"):
            v = vars_[k].get().strip()
            c[k] = int(v) if v.isdigit() else DEFAULT_CFG[k]
        c["password"] = var_pw.get()
        c["remember"] = bool(var_remember.get())
        c["skip_backup"] = bool(var_nobackup.get())
        c["skip_verify"] = bool(var_noverify.get())
        return c

    def set_running(flag: bool):
        state["running"] = flag
        for b in (btn_go, btn_dry):
            b.configure(state="disabled" if flag else "normal")
        btn_save.configure(state="disabled" if flag else "normal")

    def finish(ok: bool, summary: dict):
        def _do():
            set_running(False)
            s = format_summary(summary)
            for line in s.splitlines():
                txt.configure(state="normal")
                txt.insert("end", line + "\n", "ok" if ok else "err")
                txt.configure(state="disabled")
            txt.see("end")
            status.configure(text="部署成功" if ok else "部署失败 —— 看日志最后几行")
            bar.configure(value=100)
        root.after(0, _do)

    def job(dry: bool):
        c = collect_cfg()
        if not c["password"]:
            ui_log("err", "请先填 SSH 密码")
            pw_e.focus_set()
            return
        if c["remember"]:
            try:
                p = save_ini(c)
                ui_log("dim", f"设置已保存到 {p}")
            except Exception as e:  # noqa: BLE001
                ui_log("warn", f"保存设置失败：{e}")
        set_running(True)
        bar.configure(value=0)
        status.configure(text="开始…")
        txt.configure(state="normal")
        txt.insert("end", "\n" + "─" * 76 + "\n", "dim")
        txt.configure(state="disabled")

        d = Deployer(c, ui_log, ui_prog, finish)
        d.log(f"源码：{d.src}（{d.src_from}）")
        d.log(f"目标：{c['user']}@{c['host']}:{d.rdir}")
        if dry:
            d.log("演练模式：只比对、不写入", "warn")
        threading.Thread(target=lambda: d.deploy(dry=dry), daemon=True).start()

    def do_save():
        c = collect_cfg()
        try:
            ui_log("ok", f"设置已保存到 {save_ini(c)}")
        except Exception as e:  # noqa: BLE001
            ui_log("err", f"保存失败：{e}")

    def open_site():
        c = collect_cfg()
        import webbrowser
        webbrowser.open(f"http://{c['host']}:{c['site_port']}")

    btn_go.configure(command=lambda: job(False))
    btn_dry.configure(command=lambda: job(True))
    btn_save.configure(command=do_save)
    btn_site.configure(command=open_site)
    root.bind("<Return>", lambda _e: job(False))

    info2 = read_build_info()
    src_path2, src_from2 = resolve_source(str(cfg.get("src_dir") or ""))
    ui_log("head", f"{APP_TITLE} v{APP_VER}   （{info2.get('stamp') or '源码模式'}）")
    if not looks_like_project(src_path2):
        ui_log("err", f"源码目录不像是本项目：{src_path2}")
        ui_log("err", "请把本程序放到项目目录（或它的上一级），或在「源码目录」里填上完整路径。")
    else:
        n_tools = len(list((src_path2 / "tools").glob("*.py"))) if (src_path2 / "tools").is_dir() else 0
        ui_log("dim", f"源码：{src_path2}（{src_from2}，tools/ 下 {n_tools} 个脚本）")
    if not cfg.get("password"):
        ui_log("warn", "首次使用：请填 NAS 的 SSH 密码（用户 wza0126 的登录密码）")
        pw_e.focus_set()
        auto = False
    else:
        auto = bool(a.auto)
        if auto:
            ui_log("dim", "已读取保存的设置，2 秒后自动开始部署（直接点「开始部署」也可以）")

    if auto:
        root.after(1200, lambda: job(False))

    root.mainloop()
    return 0


# ============================================================
#  入口
# ============================================================

def hide_console():
    """双击时不弹出黑框；CLI 模式保留（排障要看输出）。"""
    if not is_frozen():
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:  # noqa: BLE001
        pass


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=f"{APP_TITLE} v{APP_VER}")
    p.add_argument("--cli", action="store_true", help="命令行模式（不弹界面）")
    p.add_argument("--dry", action="store_true", help="只比对差异，不写入任何东西")
    p.add_argument("--host", help=f"NAS 地址，默认 {D_HOST}")
    p.add_argument("--user", help=f"SSH 用户，默认 {D_USER}")
    p.add_argument("--password", help="SSH 密码（更推荐用环境变量 NAS_PW）")
    p.add_argument("--ssh-port", type=int, dest="ssh_port", help="SSH 端口，默认 22")
    p.add_argument("--site-port", type=int, dest="site_port", help=f"站点端口，默认 {D_SITE_PORT}")
    p.add_argument("--remote-root", dest="remote_root", help=f"NAS 部署根，默认 {D_ROOT}")
    p.add_argument("--src", help="要部署的源码目录（默认自动探测）")
    p.add_argument("--no-backup", action="store_true", dest="no_backup")
    p.add_argument("--no-verify", action="store_true", dest="no_verify")
    p.add_argument("--auto", type=int, default=1, help="GUI 是否自动开始（1/0，默认 1）")
    p.add_argument("--version", action="version", version=f"{APP_TITLE} {APP_VER}")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.cli:
        return run_cli(a)
    hide_console()
    return run_gui(a)


if __name__ == "__main__":
    sys.exit(main())
