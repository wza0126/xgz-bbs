# -*- coding: utf-8 -*-
"""通用工具：时间、大小、头像色、附件落盘、通知、课题材料打包。"""
import csv
import hashlib
import io
import os
import re
import unicodedata
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app

from . import db as dbm

TZ = timezone(timedelta(hours=8))

# 头像配色（够用、不刺眼）
AVATAR_COLORS = [
    ("#E6F1FB", "#0C447C"), ("#E1F5EE", "#0F6E56"), ("#FAEEDA", "#854F0B"),
    ("#EEEDFE", "#3C3489"), ("#FBEAF0", "#993556"), ("#EAF3DE", "#3B6D11"),
    ("#FAECE7", "#993C1D"), ("#F1EFE8", "#444441"),
]

_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


# ---------- 时间 ----------

def to_local(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(TZ)


def fmt_dt(ts) -> str:
    d = to_local(ts)
    return d.strftime("%Y-%m-%d %H:%M") if d else ""


def fmt_day(ts) -> str:
    d = to_local(ts)
    return d.strftime("%Y-%m-%d") if d else ""


def rel_time(ts) -> str:
    if not ts:
        return ""
    delta = datetime.now(tz=TZ) - to_local(ts)
    sec = delta.total_seconds()
    if sec < 60:
        return "刚刚"
    if sec < 3600:
        return f"{int(sec // 60)} 分钟前"
    if sec < 86400:
        return f"{int(sec // 3600)} 小时前"
    if sec < 86400 * 7:
        return f"{int(sec // 86400)} 天前"
    d = to_local(ts)
    return d.strftime("%m-%d") if d.year == datetime.now(tz=TZ).year else d.strftime("%Y-%m-%d")


def local_today() -> str:
    return datetime.now(tz=TZ).strftime("%Y-%m-%d")


def due_state(due_date):
    """返回 (文案, 是否逾期, 剩余天数)。due_date 为 'YYYY-MM-DD' 字符串。"""
    if not due_date:
        return ("未设截止", False, None)
    try:
        due = datetime.strptime(due_date, "%Y-%m-%d").date()
    except ValueError:
        return (due_date, False, None)
    today = datetime.now(tz=TZ).date()
    left = (due - today).days
    if left < 0:
        return (f"逾期 {abs(left)} 天", True, left)
    if left == 0:
        return ("今天截止", False, 0)
    return (f"还剩 {left} 天", False, left)


# ---------- 展示 ----------

def human_size(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def avatar_style(name: str) -> str:
    h = sum(ord(c) for c in (name or "?"))
    bg, fg = AVATAR_COLORS[h % len(AVATAR_COLORS)]
    return f"background:{bg};color:{fg}"


def avatar_char(name: str) -> str:
    name = (name or "?").strip()
    return name[0] if name else "?"


def file_badge(ext: str) -> str:
    ext = (ext or "").upper()
    if not ext:
        return "FILE"
    return ext[:4]


def sanitize_filename(name: str, fallback="未命名") -> str:
    name = unicodedata.normalize("NFC", (name or "").strip())
    name = _ILLEGAL.sub("_", name).strip(" .")
    if not name:
        return fallback
    if len(name) > 90:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:80] + dot + ext) if dot else name[:90]
    return name


def safe_tag(text: str, limit=40) -> str:
    return sanitize_filename((text or "").replace("\n", " "), "未命名")[:limit] or "未命名"


# ---------- 附件 ----------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def save_upload(file_storage, *, owner_id, board_id, attachable_type, attachable_id):
    """落盘 + 写 attachments 表。落盘名一律用 uuid，绝不使用原始文件名。"""
    import mimetypes

    orig = (file_storage.filename or "").replace("\\", "/").split("/")[-1].strip()
    if not orig:
        raise ValueError("没有选择文件")
    ext = orig.rsplit(".", 1)[-1].lower() if "." in orig else ""
    banned = current_app.config["BANNED_EXT"]
    if ext in banned:
        raise ValueError(f"不支持上传 .{ext} 类型的文件")

    upload_root = Path(current_app.config["UPLOAD_DIR"])
    now = datetime.now(tz=TZ)
    rel_dir = Path(f"{now.year:04d}") / f"{now.month:02d}"
    abs_dir = upload_root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    stored_name = uuid.uuid4().hex + (f".{ext}" if ext else "")
    abs_path = abs_dir / stored_name
    file_storage.save(str(abs_path))

    size = abs_path.stat().st_size
    max_bytes = current_app.config["MAX_CONTENT_LENGTH"]
    if size > max_bytes:
        abs_path.unlink(missing_ok=True)
        raise ValueError(f"文件超过 {current_app.config['MAX_UPLOAD_MB']} MB 上限")
    if size == 0:
        abs_path.unlink(missing_ok=True)
        raise ValueError("文件是空的")

    mime = file_storage.mimetype or mimetypes.guess_type(orig)[0] or "application/octet-stream"
    rel_path = str(rel_dir / stored_name).replace("\\", "/")

    return dbm.execute(
        "INSERT INTO attachments (owner_id, board_id, attachable_type, attachable_id,"
        " orig_name, stored_path, ext, mime, size_bytes, sha256, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (owner_id, board_id, attachable_type, attachable_id,
         orig[:200], rel_path, ext, mime, size, _sha256(abs_path), dbm.now_ts()),
    )


def att_abs_path(att) -> Path:
    return Path(current_app.config["UPLOAD_DIR"]) / att["stored_path"]


def delete_attachment(att, remove_file=True):
    dbm.execute("DELETE FROM attachments WHERE id = ?", (att["id"],))
    if remove_file:
        try:
            att_abs_path(att).unlink(missing_ok=True)
        except OSError:
            pass


def attachments_for(attachable_type, ids):
    """按 attachable_id 批量取附件，返回 {id: [att, ...]}。"""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rs = dbm.rows(
        f"SELECT a.*, u.display_name FROM attachments a JOIN users u ON u.id = a.owner_id"
        f" WHERE a.attachable_type = ? AND a.attachable_id IN ({marks}) ORDER BY a.id",
        tuple([attachable_type] + list(ids)),
    )
    out = {}
    for r in rs:
        out.setdefault(r["attachable_id"], []).append(r)
    return out


# ---------- 通知 ----------

def notify(user_ids, *, actor_id, kind, summary, link="", board_id=None,
           topic_id=None, task_id=None):
    ids = [u for u in dict.fromkeys(user_ids or []) if u and u != actor_id]
    if not ids:
        return
    ts = dbm.now_ts()
    dbm.execute_many(
        "INSERT INTO notifications (user_id, actor_id, kind, board_id, topic_id, task_id,"
        " summary, link, is_read, created_at) VALUES (?,?,?,?,?,?,?,?,0,?)",
        [(u, actor_id, kind, board_id, topic_id, task_id, summary, link, ts) for u in ids],
    )


def unread_count(user_id) -> int:
    if not user_id:
        return 0
    return dbm.scalar(
        "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0", (user_id,), 0
    )


def extract_mentions(text: str):
    """取 @某人 中的姓名（按显示名匹配）。"""
    if not text or "@" not in text:
        return []
    return list(dict.fromkeys(re.findall(r"@([\u4e00-\u9fa5A-Za-z0-9_]{1,20})", text)))


def users_by_display_names(names):
    if not names:
        return []
    marks = ",".join("?" * len(names))
    rs = dbm.rows(f"SELECT id FROM users WHERE display_name IN ({marks}) AND is_active = 1",
                  tuple(names))
    return [r["id"] for r in rs]


# ---------- 课题材料打包 ----------

def _txt_io(text: str) -> io.BytesIO:
    return io.BytesIO(("\ufeff" + text).encode("utf-8"))


def export_board_zip(board_id: int, mode: str = "all"):
    """
    mode='all'   打包正文 + 讨论 + 任务 + 全部附件
    mode='files' 只打包附件（扁平，带编号与来源说明）
    返回 (zip 绝对路径, 下载文件名)
    """
    board = dbm.row("SELECT * FROM boards WHERE id = ?", (board_id,))
    if board is None:
        raise ValueError("课题组不存在")

    export_dir = Path(current_app.config["BACKUP_DIR"]) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)

    stem = safe_tag(board["name"], 50)
    stamp = datetime.now(tz=TZ).strftime("%Y%m%d-%H%M")
    zip_name = f"{stem}-{stamp}{'-附件包' if mode == 'files' else ''}.zip"
    zip_path = export_dir / f"{board_id}-{uuid.uuid4().hex[:8]}.zip"

    topics = dbm.rows(
        "SELECT t.*, u.display_name AS author_name FROM topics t JOIN users u ON u.id = t.author_id"
        " WHERE t.board_id = ? ORDER BY t.created_at", (board_id,))
    topic_ids = [t["id"] for t in topics]
    posts_map = {}
    if topic_ids:
        marks = ",".join("?" * len(topic_ids))
        for p in dbm.rows(
            f"SELECT p.*, u.display_name AS author_name FROM posts p JOIN users u ON u.id = p.author_id"
            f" WHERE p.topic_id IN ({marks}) AND p.is_deleted = 0 ORDER BY p.floor_no", tuple(topic_ids)):
            posts_map.setdefault(p["topic_id"], []).append(p)

    topic_atts = attachments_for("topic", topic_ids)
    post_atts = attachments_for("post", [p["id"] for ps in posts_map.values() for p in ps])

    tasks = dbm.rows(
        "SELECT k.*, t.created_at AS topic_created, t.title AS topic_title, t.status AS topic_status,"
        " u.display_name AS creator_name FROM tasks k JOIN topics t ON t.id = k.topic_id"
        " JOIN users u ON u.id = k.created_by WHERE k.board_id = ? ORDER BY k.id", (board_id,))
    assignees = {}
    if tasks:
        marks = ",".join("?" * len(tasks))
        for a in dbm.rows(
            f"SELECT a.*, u.display_name, u.subject FROM task_assignees a JOIN users u ON u.id = a.user_id"
            f" WHERE a.task_id IN ({marks}) ORDER BY a.id", tuple(t["id"] for t in tasks)):
            assignees.setdefault(a["task_id"], []).append(a)

    # 找出哪些 post 是任务提交
    submission_of = {}
    for a in [x for v in assignees.values() for x in v]:
        if a["submission_post_id"]:
            submission_of[a["submission_post_id"]] = a

    file_index = []   # 供 CSV 与「只打包附件」使用
    seq = 0

    def add_file(zf: zipfile.ZipFile, att, arcname: str, source: str):
        nonlocal seq
        src = att_abs_path(att)
        seq += 1
        entry = {
            "序号": seq, "文件名": att["orig_name"], "大小": human_size(att["size_bytes"]),
            "上传人": att["display_name"], "所属": source,
            "上传时间": fmt_dt(att["created_at"]), "打包内路径": arcname,
        }
        file_index.append(entry)
        if src.exists():
            try:
                zf.write(str(src), arcname)
                return True
            except OSError:
                entry["打包内路径"] = arcname + "（读取失败）"
        else:
            entry["打包内路径"] = arcname + "（源文件已丢失）"
        return False

    members = dbm.rows(
        "SELECT m.role, u.display_name, u.subject FROM board_members m JOIN users u ON u.id = m.user_id"
        " WHERE m.board_id = ? ORDER BY CASE m.role WHEN 'leader' THEN 0 ELSE 1 END, m.joined_at",
        (board_id,))

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        root = f"{stem}/" if mode == "all" else ""

        if mode == "all":
            # -------- 课题概览 --------
            done = sum(1 for v in assignees.values() for a in v if a["status"] in ("done", "confirmed"))
            total = sum(len(v) for v in assignees.values())
            scored = [(a["display_name"], a["score"]) for v in assignees.values() for a in v
                      if a["score"] is not None]
            lines = [
                f"课题组：{board['name']}",
                f"类型　：{'课题研究组' if board['kind'] == 'project' else '综合教研组'}"
                f"{'（公开组）' if board['is_public'] else ''}",
                f"简介　：{board['description'] or '（无）'}",
                f"创建　：{fmt_dt(board['created_at'])}",
                f"导出　：{fmt_dt(dbm.now_ts())}",
                "",
                f"话题总数：{len(topics)}　任务总数：{len(tasks)}　"
                f"任务完成情况：{done}/{total}",
                "",
                "成员名单：",
            ]
            for m in members:
                lines.append(f"  · {m['display_name']}"
                             f"（{m['subject'] or '未填学科'}）{' —— 组长' if m['role'] == 'leader' else ''}")
            if scored:
                lines += ["", "任务得分："]
                agg = {}
                for name, sc in scored:
                    agg.setdefault(name, []).append(sc)
                for name, scs in agg.items():
                    lines.append(f"  · {name}：{sum(scs)} 分（{len(scs)} 项）")
            zf.writestr(root + "_课题概览.txt", _txt_io("\n".join(lines)).getvalue())

        # -------- 话题与讨论 --------
        for idx, t in enumerate(topics, 1):
            if mode == "all":
                kind = {"discussion": "讨论", "notice": "公告", "task": "任务"}.get(t["kind"], t["kind"])
                head = [
                    f"【{kind}】{t['title']}",
                    f"作者：{t['author_name']}　发布：{fmt_dt(t['created_at'])}"
                    f"　浏览：{t['view_count']}　回复：{t['reply_count']}",
                    f"状态：{'已关闭' if t['status'] == 'closed' else '进行中'}"
                    f"{'　[置顶]' if t['is_pinned'] else ''}{'　[精]' if t['is_featured'] else ''}",
                    "-" * 56, "", t["body"] or "（无正文）", "",
                ]
                post_lines = []
                for p in posts_map.get(t["id"], []):
                    who = p["author_name"]
                    if p["parent_id"]:
                        post_lines.append(f"    └ [{p['floor_no']}楼 回复] {who}（{fmt_dt(p['created_at'])}）：{p['body']}")
                    else:
                        post_lines.append(f"[{p['floor_no']}楼] {who}（{fmt_dt(p['created_at'])}）：{p['body']}")
                    for att in post_atts.get(p["id"], []):
                        post_lines.append(f"      · 附件：{att['orig_name']}（{human_size(att['size_bytes'])}）")
                head += post_lines or ["（暂无讨论）"]
                folder = f"{root}话题/{idx:03d}_{safe_tag(t['title'], 40)}/"
                zf.writestr(folder + "_正文与讨论.txt", _txt_io("\n".join(head)).getvalue())
                for att in topic_atts.get(t["id"], []):
                    arc = f"{folder}_附件/{att['id']:04d}_{safe_tag(att['orig_name'], 60)}"
                    add_file(zf, att, arc, f"话题「{t['title']}」附件")
            else:
                for att in topic_atts.get(t["id"], []):
                    arc = f"{seq + 1:04d}_{safe_tag(att['orig_name'], 60)}"
                    add_file(zf, att, arc, f"话题「{t['title']}」附件")

            # 该话题下非提交性质的回复附件
            for p in posts_map.get(t["id"], []):
                if p["id"] in submission_of:
                    continue
                for att in post_atts.get(p["id"], []):
                    if mode == "all":
                        folder = f"{root}话题/{idx:03d}_{safe_tag(t['title'], 40)}/_附件/"
                        arc = f"{folder}{p['floor_no']:03d}_{safe_tag(p['author_name'], 12)}_{safe_tag(att['orig_name'], 60)}"
                    else:
                        arc = f"{seq + 1:04d}_{safe_tag(att['orig_name'], 60)}"
                    add_file(zf, att, arc, f"话题「{t['title']}」{p['floor_no']}楼 {p['author_name']}")

        # -------- 任务 --------
        for idx, k in enumerate(tasks, 1):
            rows_a = assignees.get(k["id"], [])
            kfolder = f"{root}任务/{idx:03d}_{safe_tag(k['title'], 40)}/"
            if mode == "all":
                lines = [
                    f"任务：{k['title']}",
                    f"发布：{k['creator_name']}　{fmt_dt(k['topic_created'])}",
                    f"截止：{k['due_date'] or '未设'}　满分：{k['points'] or '不计分'}",
                    f"说明：{k['detail'] or '（无）'}",
                    "-" * 56, "", "完成情况：",
                ]
                for a in rows_a:
                    st = {"todo": "待办", "doing": "进行中", "done": "已提交",
                          "confirmed": "已通过", "rejected": "已打回"}.get(a["status"], a["status"])
                    score = f"　得分：{a['score']}" if a["score"] is not None else ""
                    lines.append(f"  · {a['display_name']}：{st}{score}"
                                 f"{'　提交于 ' + fmt_dt(a['submitted_at']) if a['submitted_at'] else ''}")
                    if a["review_note"]:
                        lines.append(f"      组长意见：{a['review_note']}")
                    sub = next((p for p in posts_map.get(k["topic_id"], [])
                                if p["id"] == a["submission_post_id"]), None)
                    if sub and sub["body"]:
                        lines.append(f"      提交说明：{sub['body']}")
                    for att in post_atts.get(a["submission_post_id"], []) if a["submission_post_id"] else []:
                        lines.append(f"      附件：{att['orig_name']}（{human_size(att['size_bytes'])}）")
                zf.writestr(kfolder + "_任务说明与完成情况.txt", _txt_io("\n".join(lines)).getvalue())

            for a in rows_a:
                if not a["submission_post_id"]:
                    continue
                for att in post_atts.get(a["submission_post_id"], []):
                    if mode == "all":
                        arc = (f"{kfolder}_提交附件/"
                               f"{safe_tag(a['display_name'], 12)}_{safe_tag(att['orig_name'], 60)}")
                    else:
                        arc = f"{seq + 1:04d}_{safe_tag(att['orig_name'], 60)}"
                    add_file(zf, att, arc, f"任务「{k['title']}」{a['display_name']}提交")

        # -------- 附件总清单 --------
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["序号", "文件名", "大小", "上传人", "所属", "上传时间", "打包内路径"])
        writer.writeheader()
        for e in file_index:
            writer.writerow(e)
        zf.writestr((root or "") + "_附件总清单.csv",
                    ("\ufeff" + buf.getvalue()).encode("utf-8"))

    return zip_path, zip_name
