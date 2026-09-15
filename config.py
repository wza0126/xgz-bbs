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

TOPIC_KIND = {"discussion": "讨论", "notice": "公告", "task": "任务",
              "training": "培训", "achievement": "成果"}


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
