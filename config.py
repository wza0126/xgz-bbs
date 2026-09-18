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

# 话题类型。键即 topics.kind 存库值，值即界面显示的中文名。
# 新增类型时只需改这里一处（外加 app.css 里加一条 .t-<kind> 配色），
# 标签栏、发帖类型选择、kind 白名单都从这里派生。
TOPIC_KIND = {
    "discussion": "讨论",
    "plan": "方案",
    "record": "记录",
    "work": "作品",
    "achievement": "成果",
    "training": "培训",
    "resource": "资源",
    "notice": "公告",
    "task": "任务",
}

# 课题组页标签栏的排列顺序（「全部」由模板补在最前）
TOPIC_KIND_ORDER = ["task", "discussion", "notice", "training", "achievement",
                    "plan", "record", "work", "resource"]

# 仅组长可发布的类型；其余类型组员也能发
LEADER_ONLY_KINDS = ("notice", "task")

# 可置顶的类型
PINNABLE_KINDS = ("notice", "task")

# 组员可发布的类型名（按标签栏顺序）——发帖页提示文案直接用，别再手写一遍
TOPIC_KIND_FREE_LABELS = [TOPIC_KIND[k] for k in TOPIC_KIND_ORDER
                          if k not in LEADER_ONLY_KINDS]


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
