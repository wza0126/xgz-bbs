#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把「便携演示版」打成单文件 exe。

产出：
    dist/行知教研吧-演示包/
        行知教研吧-演示版.exe      <-- 双击就跑
        使用说明.txt

和 build_deployer.py 的区别（别搞混了）：
    build_deployer.py  -> 行知教研吧-一键部署.exe  往 NAS 上推代码
    build_portable.py  -> 行知教研吧-演示版.exe    在本机起服务，给别人看

用法：
    <打包venv>/python tools/build_portable.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "tools" / "portable_app.py"
BUILD_DIR = ROOT / "build" / "portable"
DIST_DIR = ROOT / "dist"
PACK_DIR = DIST_DIR / "行知教研吧-演示包"
EXE_NAME = "行知教研吧-演示版"

# 模板 / 样式 / 建库脚本这些「不是 .py 的文件」不会被自动带上，必须显式塞进去。
# 目标路径要跟 app 包在 exe 内部的落点对齐 —— Flask 的 root_path 会指到 _MEIPASS/app，
# 所以 schema.sql 落在 app/ 下、模板落在 app/templates/ 下。
DATAS = [
    (ROOT / "app" / "templates", "app/templates"),
    (ROOT / "app" / "static", "app/static"),
    (ROOT / "app" / "schema.sql", "app"),
]

REQUIRED = [
    ROOT / "app" / "__init__.py",
    ROOT / "app" / "schema.sql",
    ROOT / "app" / "templates" / "base.html",
    ROOT / "app" / "static" / "css" / "app.css",
    ROOT / "config.py",
    ENTRY,
]

README = """行知教研吧 · 便携演示版
============================================================

【怎么用】
  1. 把这个文件夹整个拷到要演示的电脑上（U 盘、桌面、D 盘哪儿都行）。
  2. 把正式那台电脑上的 data 文件夹，拷到这个文件夹里，
     和「行知教研吧-演示版.exe」并排放，像这样：

        行知教研吧-演示包\\
            行知教研吧-演示版.exe
            使用说明.txt
            data\\                <-- 从正式那台拷过来的
                bbs.db
                uploads\\
                secret.key

  3. 双击「行知教研吧-演示版.exe」。
     窗口里会列出几个 http://192.168.x.x:8009 这样的地址，
     本机会自动打开浏览器；想让别人看，就把那个地址发给他们
     （对方要和你连同一个 WiFi / 同一个交换机）。

  4. 演示完，在窗口里按 Ctrl+C 就停；直接关窗口也行。

【没有 data 文件夹也能跑】
  第一次启动会自动建一个空库，默认管理员 admin / admin123。
  想演示真实内容，还是把 data 拷过来更合适。

【别人打不开怎么办】
  · 第一次运行时 Windows 会弹「防火墙」询问，一定要点「允许访问」。
  · 确认对方和你连的是同一个网络（看 IP 前三段是否一样，比如都是 192.168.1）。
  · 8009 被别的程序占了也没关系，程序会自动改用 8009 之后的端口，
    具体用哪个端口，看窗口里「端口提示」那一行。

【想换端口 / 换数据目录】
  在命令行里跑：
      行知教研吧-演示版.exe --port 8080
      行知教研吧-演示版.exe --data-dir D:\\我的演示数据
      行知教研吧-演示版.exe --no-browser      （不自动开浏览器）

【注意】
  · 所有数据都写在 exe 同级的 data 文件夹里，演示期间别人发的帖子
    也改的是这份拷贝，不会影响正式那台。
  · 文件路径不要放太深，也别放在 C:\\Program Files 里（那里需要管理员权限）。
"""


def check_env() -> None:
    missing = [str(p) for p in REQUIRED if not p.exists()]
    if missing:
        print("[x] 项目文件不全，缺：")
        for m in missing:
            print("    " + m)
        sys.exit(2)
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[x] 当前 Python 环境没装 PyInstaller。先：pip install pyinstaller")
        sys.exit(2)


def make_icon(out_path: Path) -> Path | None:
    """画一个应用图标。画不出来就返回 None，不影响打包。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[i] 没装 pillow，跳过图标")
        return None

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 底：竖直渐变圆角方块
    top, bottom = (59, 130, 246), (29, 64, 175)
    grad = Image.new("RGB", (1, size))
    gp = grad.load()
    for y in range(size):
        k = y / (size - 1)
        gp[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * k) for i in range(3))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=56, fill=255)
    img.paste(grad.resize((size, size)), (0, 0), mask)

    # 中间一个白色「行」字
    font = None
    for fp in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
               r"C:\Windows\Fonts\simhei.ttf"):
        if Path(fp).exists():
            try:
                font = ImageFont.truetype(fp, 148)
                break
            except OSError:
                continue

    if font:
        box = d.textbbox((0, 0), "行", font=font)
        d.text(((size - box[2] + box[0]) / 2, (size - box[3] + box[1]) / 2 - 12),
               "行", font=font, fill=(255, 255, 255, 255))
    else:
        # 没有中文字体就画三条横线，凑合能看
        for i, y in enumerate((88, 128, 168)):
            w = (140, 110, 90)[i]
            d.rounded_rectangle([(size - w) / 2, y, (size + w) / 2, y + 18],
                                radius=9, fill=(255, 255, 255, 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                              (64, 64), (128, 128), (256, 256)])
    return out_path


def build(clean: bool = True) -> Path:
    check_env()

    if BUILD_DIR.exists() and clean:
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    icon = make_icon(BUILD_DIR / "app.ico")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",                 # 单文件，方便整个拷走
        "--console",                 # 留个窗口显示访问地址，老师要照着念给对方
        "--noconfirm",
        "--clean",
        "--name", EXE_NAME,
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        "--specpath", str(BUILD_DIR),
        "--paths", str(ROOT),
        "--collect-submodules", "waitress",   # waitress 是运行时才 import 的，点名收进来
        "--hidden-import", "app.views.admin",
    ]
    if icon:
        cmd += ["--icon", str(icon)]
    for src, dest in DATAS:
        cmd += ["--add-data", f"{src}{os.pathsep}{dest}"]
    cmd.append(str(ENTRY))

    print("[i] 开始打包 …")
    print("    " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        print("[x] 打包失败")
        sys.exit(r.returncode)

    exe = DIST_DIR / f"{EXE_NAME}.exe"
    if not exe.exists():
        print("[x] 没找到产物：" + str(exe))
        sys.exit(2)

    # 摆一个「拷走就能用」的文件夹
    PACK_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, PACK_DIR / exe.name)
    (PACK_DIR / "使用说明.txt").write_text(README, encoding="utf-8")

    size_mb = exe.stat().st_size / 1024 / 1024
    print()
    print("=" * 62)
    print(f"  [√] 打包完成：{exe}")
    print(f"      体积 {size_mb:.1f} MB")
    print(f"  [√] 演示包目录：{PACK_DIR}")
    print("      把这个文件夹拷到笔记本，data/ 放进去，双击 exe 就行。")
    print("=" * 62)
    return exe


if __name__ == "__main__":
    build()
