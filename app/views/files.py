# -*- coding: utf-8 -*-
"""附件：上传（含草稿认领）、下载、页内预览、删除。"""
from pathlib import Path

from flask import (Blueprint, abort, current_app, jsonify, render_template, request,
                   send_file, url_for)

from .. import auth as authm
from .. import db as dbm
from .. import utils

bp = Blueprint("files", __name__)


# ---------- 上传表单辅助 ----------

def form_attach_ids(req):
    return [int(x) for x in req.form.getlist("attach_ids") if x.isdigit()]


def claim_attachments(ids, *, user_id, attachable_type, attachable_id, board_id):
    """把上传阶段产生的草稿附件认领到真正的宿主（话题 / 回复）上。"""
    for att_id in dict.fromkeys(ids or []):
        dbm.execute(
            "UPDATE attachments SET attachable_type = ?, attachable_id = ?, board_id = ?"
            " WHERE id = ? AND owner_id = ? AND attachable_type = 'draft'",
            (attachable_type, attachable_id, board_id, att_id, user_id))


# ---------- 上传 ----------

@bp.route("/upload", methods=["POST"])
@authm.login_required
def upload():
    user = authm.current_user()
    fs = request.files.get("file")
    if fs is None or not fs.filename:
        return jsonify(ok=False, error="没有收到文件"), 400

    board_id = request.form.get("board_id")
    board_id = int(board_id) if (board_id or "").isdigit() else None
    if board_id is None:
        return jsonify(ok=False, error="缺少课题组参数"), 400
    if not authm.is_member(user, board_id):
        return jsonify(ok=False, error="你不是这个课题组的成员"), 403

    a_type = request.form.get("attachable_type") or "draft"
    a_id = request.form.get("attachable_id")
    a_id = int(a_id) if (a_id or "").isdigit() else 0
    if a_type not in ("draft", "topic", "post"):
        a_type, a_id = "draft", 0
    if a_type == "draft":
        # 草稿阶段还没目标，等表单提交时再认领
        pass

    try:
        att_id = utils.save_upload(fs, owner_id=user["id"], board_id=board_id,
                                   attachable_type=a_type, attachable_id=a_id)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    except OSError:
        current_app.logger.exception("附件落盘失败")
        return jsonify(ok=False, error="文件保存失败，请检查磁盘空间"), 500

    att = dbm.row("SELECT * FROM attachments WHERE id = ?", (att_id,))
    return jsonify(
        ok=True, id=att_id, name=att["orig_name"], ext=att["ext"],
        badge=utils.file_badge(att["ext"]),
        size=att["size_bytes"], size_text=utils.human_size(att["size_bytes"]),
        url=url_for("files.download", attach_id=att_id),
        preview=url_for("files.inline", attach_id=att_id)
        if att["ext"] in current_app.config["INLINE_EXT"] else None,
        delete_url=url_for("files.delete", attach_id=att_id),
    )


# ---------- 读取 ----------

def _get_att_or_404(attach_id):
    att = dbm.row("SELECT a.*, u.display_name FROM attachments a JOIN users u ON u.id = a.owner_id"
                  " WHERE a.id = ?", (attach_id,))
    if att is None:
        abort(404)
    board = authm.board_of_attachment(att)
    if not authm.can_view_board(board, authm.current_user()):
        abort(403)
    return att, board


@bp.route("/f/<int:attach_id>")
@authm.login_required
def download(attach_id):
    att, _board = _get_att_or_404(attach_id)
    path = utils.att_abs_path(att)
    if not path.exists():
        abort(404)
    dbm.execute("UPDATE attachments SET download_count = download_count + 1 WHERE id = ?", (attach_id,))
    return send_file(str(path), as_attachment=True,
                     download_name=att["orig_name"],
                     mimetype=att["mime"] or "application/octet-stream")


@bp.route("/f/<int:attach_id>/inline")
@authm.login_required
def inline(attach_id):
    att, _board = _get_att_or_404(attach_id)
    if att["ext"] not in current_app.config["INLINE_EXT"]:
        return redirect_download(attach_id)
    path = utils.att_abs_path(att)
    if not path.exists():
        abort(404)
    return send_file(str(path), as_attachment=False, download_name=att["orig_name"],
                     mimetype=att["mime"] or "application/octet-stream")


def redirect_download(attach_id):
    from flask import redirect
    return redirect(url_for("files.download", attach_id=attach_id))


# ---------- 删除 ----------

@bp.route("/f/<int:attach_id>/delete", methods=["POST"])
@authm.login_required
def delete(attach_id):
    att, _board = _get_att_or_404(attach_id)
    user = authm.current_user()
    if att["owner_id"] != user["id"] and not authm.is_leader(user, att["board_id"]):
        abort(403)
    utils.delete_attachment(att)
    if request.headers.get("X-Requested-With") == "fetch" or request.is_json:
        return jsonify(ok=True)
    flash_ok = request.form.get("back")
    from flask import flash, redirect
    flash("附件已删除", "ok")
    return redirect(flash_ok or url_for("boards.home"))
