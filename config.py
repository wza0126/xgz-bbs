# -*- coding: utf-8 -*-
"""集中配置。改这里就够了，不用翻代码。"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _resolve_data_dir() -> Path:
    raw = os.environ.get("BBS_DATA_DIR")
    if raw:
        return Path(raw).expanduser()
    return BASE_DIR / "data"


DATA_DIR = _resolve_data_dir()
DB_PATH = DATA_DIR / "bbs.db"
UPLOAD_DIR = DATA_DIR / "uploads"
BACKUP_DIR = DATA_DIR / "backups"
SECRET_FILE = DATA_DIR / "secret.key"

SITE_NAME = os.environ.get("BBS_SITE_NAME", "行知教研吧")
SITE_SUBTITLE = os.environ.get("BBS_SITE_SUBTITLE", "校内教师教研协作平台")

# 单文件上传上限（MB）
MAX_UPLOAD_MB = int(os.environ.get("BBS_MAX_UPLOAD_MB", "50"))
MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024

# 禁止上传的扩展名。默认只拦真正危险的可执行/脚本类，
# 老师要传 .py / .js / .jar 教具时不受影响。
BANNED_EXT = {
    "exe", "bat", "cmd", "com", "scr", "msi", "lnk", "reg",
    "ps1", "psm1", "vbs", "vbe", "dll", "hta", "cpl", "inf",
    "jsp", "asp", "aspx", "php", "cgi", "sh",
}

# 可直接在页内预览的扩展名
INLINE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "pdf", "txt", "md", "log", "csv"}

PER_PAGE = 20
TIMEZONE_OFFSET_HOURS = 8          # Asia/Shanghai

# 任务指派状态
TASK_STATUS = {
    "todo": "待办",
    "doing": "进行中",
    "done": "已提交",
    "confirmed": "已通过",
    "rejected": "已打回",
}

# ============================================================
#  话题类型（kind）
# ------------------------------------------------------------
#  清单存在数据库表 topic_kinds 里，管理员在「管理后台 → 类型标签」自助增删，
#  不用再改代码、不用重新部署。下面这几份只在两种场合用：
#    ① 首次建库时灌种子（app/kinds.py: seed_defaults）
#    ② 表读不到时兜底，保证页面照样能渲染
#  读取入口统一走 app/kinds.py，别在别处再写死类型列表。
# ============================================================

# 标签配色调色板。数据库里只存这个键，CSS 里对应 .t-c-<键>，
# 所以后台新增类型不用动 CSS。顺序即后台颜色选择的顺序。
PALETTE = {
    "brand": "蓝",
    "ok": "绿",
    "warn": "琥珀",
    "danger": "红",
    "purple": "紫",
    "teal": "青",
    "rose": "玫红",
    "orange": "橙",
    "slate": "石板灰",
    "soft": "浅灰",
}

# 种子类型：code = 入库值 / 界面网址标识，label = 中文名，
# color = 调色板键，leader_only = 仅组长可发，pinnable = 可置顶
DEFAULT_TOPIC_KINDS = [
    {"code": "task", "label": "任务", "color": "brand",
     "leader_only": 1, "pinnable": 1},
    {"code": "discussion", "label": "讨论", "color": "soft",
     "leader_only": 0, "pinnable": 0},
    {"code": "notice", "label": "公告", "color": "warn",
     "leader_only": 1, "pinnable": 1},
    {"code": "training", "label": "培训", "color": "ok",
     "leader_only": 0, "pinnable": 0},
    {"code": "achievement", "label": "成果", "color": "purple",
     "leader_only": 0, "pinnable": 0},
    {"code": "plan", "label": "方案", "color": "teal",
     "leader_only": 0, "pinnable": 0},
    {"code": "record", "label": "记录", "color": "rose",
     "leader_only": 0, "pinnable": 0},
    {"code": "work", "label": "作品", "color": "orange",
     "leader_only": 0, "pinnable": 0},
    {"code": "resource", "label": "资源", "color": "slate",
     "leader_only": 0, "pinnable": 0},
]

# 代码里有专门流程、必须存在的类型：
#   discussion —— 类型非法时的兜底值（所以还不许停用）
#   task       —— 会建 tasks 行、进「我的任务」、带指派与审核
#   notice     —— 发布时通知全组
# 这三个不许删除、不许改 code。停用是允许的（可逆、不破坏历史数据），
# 但 discussion 例外 —— 它一停，发布页就没有默认类型了。
BUILTIN_KIND_CODES = ("discussion", "notice", "task")
UNDISABLABLE_KIND_CODES = ("discussion",)

# 类型非法 / 表单没带值时回落到哪个类型
FALLBACK_KIND = "discussion"

KIND_LABEL_MAX = 8          # 类型名最长几个字
KIND_CODE_RE = r"[a-z][a-z0-9_]{1,23}"   # 自定义标识的格式

# ---------- 兜底清单（数据库读不到时用，正常路径不查这里）----------
TOPIC_KIND = {k["code"]: k["label"] for k in DEFAULT_TOPIC_KINDS}
TOPIC_KIND_ORDER = [k["code"] for k in DEFAULT_TOPIC_KINDS]
TOPIC_KIND_COLOR = {k["code"]: k["color"] for k in DEFAULT_TOPIC_KINDS}
LEADER_ONLY_KINDS = tuple(k["code"] for k in DEFAULT_TOPIC_KINDS if k["leader_only"])
PINNABLE_KINDS = tuple(k["code"] for k in DEFAULT_TOPIC_KINDS if k["pinnable"])
TOPIC_KIND_FREE_LABELS = [k["label"] for k in DEFAULT_TOPIC_KINDS
                          if not k["leader_only"]]


def load_secret_key() -> str:
    """密钥优先取环境变量；否则在数据目录生成并持久化，保证重启后登录态不失效。"""
    env_key = os.environ.get("BBS_SECRET_KEY")
    if env_key:
        return env_key
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if SECRET_FILE.exists():
            key = SECRET_FILE.read_text(encoding="utf-8").strip()
            if key:
                return key
        key = secrets.token_urlsafe(48)
        SECRET_FILE.write_text(key, encoding="utf-8")
        try:
            os.chmod(SECRET_FILE, 0o600)
        except OSError:
            pass
        return key
    except OSError:
        return secrets.token_urlsafe(48)
