#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 tools/deploy_nas.py 打包成单文件 exe。

做四件事：
  1) 按和部署器**同一套规则**收集源码，快照到 build/deploy_pack/deploy_snapshot/
     （复用 deploy_nas.collect，保证 exe 里带的文件跟真正会同步的文件永远一致）
  2) 写 _build_info.json（打包时间 + git sha + 文件数）
  3) 生成图标
  4) 调 PyInstaller 出单文件 exe

用法：
    python tools/build_deployer.py

产物：
    dist/行知教研吧-一键部署.exe
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import deploy_nas as D  # noqa: E402

PACK = ROOT / "build" / "deploy_pack"
SNAP = PACK / D.SNAPSHOT_DIRNAME
PYI_OUT = PACK / "pyi"
DIST = ROOT / "dist"
EXE_ASCII = "xgz-deploy"
EXE_FINAL = "行知教研吧-一键部署.exe"
ICON = PACK / "deploy.ico"


def make_icon() -> Path | None:
    """画一个简单的应用图标（圆角蓝底 + 白色「知」）；画不出来就跳过，不阻断打包。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("  （没装 pillow，跳过图标）")
        return None
    try:
        base = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        d = ImageDraw.Draw(base)
        # 竖向渐变底
        for y in range(256):
            t = y / 255
            d.line([(0, y), (256, y)],
                   fill=(int(26 + 12 * t), int(77 + 26 * t), int(143 + 40 * t), 255))
        # 圆角蒙版
        mask = Image.new("L", (256, 256), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, 255, 255], radius=52, fill=255)
        base.putalpha(mask)
        d = ImageDraw.Draw(base)

        font = None
        for cand in ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc",
                     "C:/Windows/Fonts/simhei.ttf"):
            if Path(cand).is_file():
                try:
                    font = ImageFont.truetype(cand, 150)
                    break
                except Exception:  # noqa: BLE001
                    pass
        if font:
            txt = "知"
            box = d.textbbox((0, 0), txt, font=font)
            d.text(((256 - (box[2] - box[0])) / 2 - box[0],
                    (256 - (box[3] - box[1])) / 2 - box[1]),
                   txt, font=font, fill=(255, 255, 255, 255))
        # 右下角一个上传箭头，暗示「部署」
        d.polygon([(196, 178), (196, 130), (176, 130), (206, 96),
                   (236, 130), (216, 130), (216, 178)], fill=(255, 255, 255, 235))

        ICON.parent.mkdir(parents=True, exist_ok=True)
        base.save(ICON, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        print(f"  图标 -> {ICON}")
        return ICON
    except Exception as e:  # noqa: BLE001
        print(f"  （生成图标失败：{e}，跳过）")
        return None


def build_snapshot() -> int:
    """把源码同步到快照目录。

    刻意**不做整目录删除**：只按需补删「上次在、这次不在」的那几个文件。
    正常情况（源码只增不减）删 0 个 —— 既安全又不触发任何批量删除保护。
    """
    SNAP.mkdir(parents=True, exist_ok=True)

    d = D.Deployer(dict(D.DEFAULT_CFG))
    items = d.collect()
    want = set()
    total = 0
    for it in items:
        dst = SNAP / it.rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(it.local, dst)
        want.add(it.rel)
        total += dst.stat().st_size

    gone = 0
    for p in sorted(SNAP.rglob("*"), reverse=True):
        if p.is_file():
            rel = p.relative_to(SNAP).as_posix()
            if rel not in want and rel != "_build_info.json":
                p.unlink()
                gone += 1
        else:
            try:
                p.rmdir()
            except OSError:
                pass
    print(f"  快照 {len(items)} 个文件 / {D.human_size(total)}"
          + (f"（清掉 {gone} 个已删掉的旧文件）" if gone else ""))
    return len(items)


def write_info(n: int):
    info = {
        "stamp": time.strftime("%Y-%m-%d %H:%M"),
        "git": D.local_git_sha(ROOT),
        "files": n,
        "app_ver": D.APP_VER,
    }
    (SNAP / "_build_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(f"  构建信息 {info}")


def build_exe(icon: Path | None) -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onefile", "--console",
        "--name", EXE_ASCII,
        "--distpath", str(DIST),
        "--workpath", str(PYI_OUT),
        "--specpath", str(PACK),
        "--add-data", f"{SNAP}{';' if sys.platform == 'win32' else ':'}{D.SNAPSHOT_DIRNAME}",
        "--hidden-import", "paramiko",
        "--collect-all", "cryptography",
        "--collect-all", "bcrypt",
        "--collect-all", "nacl",
        "--collect-all", "cffi",
        "--exclude-module", "pytest",
        "--exclude-module", "numpy",
        "--exclude-module", "PIL",
        "--noupx",
        str(TOOLS / "deploy_nas.py"),
    ]
    if icon:
        cmd[cmd.index("--noupx"):cmd.index("--noupx")] = ["--icon", str(icon)]
    print("  " + " ".join(f'"{c}"' if " " in c else c for c in cmd[3:9]) + " ...")
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-4000:])
        print(r.stderr[-4000:])
        raise SystemExit(f"PyInstaller 失败（退出码 {r.returncode}）")

    src = DIST / (EXE_ASCII + (".exe" if sys.platform == "win32" else ""))
    if not src.is_file():
        raise SystemExit(f"没找到产物 {src}")
    final = DIST / EXE_FINAL
    if final.exists():
        final.unlink()
    src.replace(final)
    return final


def main() -> int:
    print("=" * 68)
    print(f"  打包 {D.APP_TITLE} v{D.APP_VER}")
    print("=" * 68)

    print("[1/4] 生成图标")
    icon = make_icon()

    print("[2/4] 收集源码快照")
    n = build_snapshot()
    write_info(n)

    print("[3/4] 调 PyInstaller（第一次会慢一些）")
    exe = build_exe(icon)

    print("[4/4] 完成")
    size = exe.stat().st_size
    print("=" * 68)
    print(f"  ✅ {exe}")
    print(f"     大小 {size / 1024 / 1024:.1f} MB")
    print("     双击即可部署；想放别处一起拷走 deploy.ini 也行（不放会重新问密码）")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
