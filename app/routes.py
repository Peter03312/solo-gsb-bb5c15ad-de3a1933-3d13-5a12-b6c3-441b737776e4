"""Web 路由：探方 / 层位 / 覆盖关系 / 遗物批次 / 回放 / 审计。"""
from __future__ import annotations

import csv
import io
import json

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    get_flashed_messages,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from . import db
from .graph import validate_relation
from .layout import layered_layout
from .replay import build_frames
from .utils import dumps, loads, now_iso

bp = Blueprint("main", __name__)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

ACTIONS = {
    "unit_create": "创建探方",
    "stratum_open": "开放层位",
    "stratum_close": "关闭层位",
    "relation_add": "登记覆盖关系",
    "relation_reject": "拒绝覆盖关系",
    "artifact_register": "登记遗物批次",
    "artifact_reject": "拒绝遗物登记",
    "draft_apply": "草稿补录成功",
    "draft_discard": "丢弃草稿",
    "actor_set": "设置操作员",
    "export_audit": "导出审计记录",
}


def audit(action: str, *, result: str = "success", unit_id: int | None = None,
          unit_code: str = "", entity: str = "", detail: str = "",
          conflict_path: str = "", actor: str | None = None) -> None:
    if actor is None:
        actor = session.get("actor", "匿名")
    db.get_db().execute(
        """INSERT INTO audit_log
           (ts, unit_id, unit_code, actor, action, result, entity, detail,
            conflict_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (now_iso(), unit_id, unit_code, actor, action, result, entity, detail,
         conflict_path),
    )


def get_unit_or_404(unit_id: int) -> dict:
    row = db.get_db().execute(
        "SELECT * FROM units WHERE id = ?", (unit_id,)
    ).fetchone()
    if row is None:
        abort(404)
    return row


def fetch_strata(unit_id: int) -> list[dict]:
    return db.get_db().execute(
        "SELECT * FROM strata WHERE unit_id = ? ORDER BY excavation_order, id",
        (unit_id,),
    ).fetchall()


def fetch_relations(unit_id: int) -> list[dict]:
    return db.get_db().execute(
        """SELECT r.*, su.code AS upper_code, sl.code AS lower_code
           FROM relations r
           JOIN strata su ON su.id = r.upper_id
           JOIN strata sl ON sl.id = r.lower_id
           WHERE r.unit_id = ? ORDER BY r.id""",
        (unit_id,),
    ).fetchall()


def fetch_batches(unit_id: int) -> list[dict]:
    return db.get_db().execute(
        """SELECT b.*, s.code AS stratum_code
           FROM batches b JOIN strata s ON s.id = b.stratum_id
           WHERE b.unit_id = ? ORDER BY b.id""",
        (unit_id,),
    ).fetchall()


def fetch_drafts(unit_id: int) -> list[dict]:
    rows = db.get_db().execute(
        "SELECT * FROM drafts WHERE unit_id = ? ORDER BY id DESC", (unit_id,)
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["payload_json"] = loads(d["payload"]) if d["payload"] else {}
        result.append(d)
    return result


def path_label(path: list[int], strata_map: dict[int, dict]) -> str:
    return " → ".join(strata_map[i]["code"] for i in path if i in strata_map)


def parse_int(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def resolve_matching_drafts(kind: str, unit_id: int, payload: dict,
                            unit_code: str) -> int:
    """成功提交后，将内容相同的保留草稿标记为 applied，返回条数。"""
    conn = db.get_db()
    count = 0
    for d in conn.execute(
        "SELECT * FROM drafts WHERE kind = ? AND unit_id = ? AND status = 'retained'",
        (kind, unit_id),
    ).fetchall():
        if loads(d["payload"]) == payload:
            conn.execute(
                "UPDATE drafts SET status = 'applied', resolved_at = ? WHERE id = ?",
                (now_iso(), d["id"]),
            )
            audit("draft_apply", unit_id=unit_id, unit_code=unit_code,
                  entity=f"草稿 #{d['id']}", detail="相同内容已成功提交，草稿自动核销")
            count += 1
    return count


# ---------------------------------------------------------------------------
# 首页 / 探方
# ---------------------------------------------------------------------------

@bp.route("/")
def index():
    units = db.get_db().execute(
        """SELECT u.*,
                  (SELECT COUNT(*) FROM strata s WHERE s.unit_id = u.id)
                      AS stratum_count,
                  (SELECT COUNT(*) FROM batches b WHERE b.unit_id = u.id)
                      AS batch_count
           FROM units u ORDER BY u.id DESC"""
    ).fetchall()
    return render_template("index.html", units=units)


@bp.route("/units", methods=["POST"])
def create_unit():
    code = request.form.get("code", "").strip()
    name = request.form.get("name", "").strip()
    note = request.form.get("note", "").strip()
    if not code:
        flash("探方编号不能为空。", "error")
        return redirect(url_for("main.index"))

    conn = db.get_db()
    try:
        cur = conn.execute(
            "INSERT INTO units (code, name, note, created_at) VALUES (?, ?, ?, ?)",
            (code, name, note, now_iso()),
        )
    except Exception:
        conn.rollback()
        flash(f"探方 {code} 已存在，编号必须唯一。", "error")
        return redirect(url_for("main.index"))

    audit("unit_create", unit_id=cur.lastrowid, unit_code=code,
          entity=code, detail=f"创建探方 {code}" + (f"（{name}）" if name else ""))
    conn.commit()
    flash(f"探方 {code} 已创建。", "success")
    return redirect(url_for("main.unit_detail", unit_id=cur.lastrowid))


@bp.route("/units/<int:unit_id>")
def unit_detail(unit_id: int):
    unit = get_unit_or_404(unit_id)
    strata = fetch_strata(unit_id)
    relations = fetch_relations(unit_id)
    batches = fetch_batches(unit_id)
    drafts = fetch_drafts(unit_id)

    counts: dict[int, int] = {}
    for b in batches:
        counts[b["stratum_id"]] = counts.get(b["stratum_id"], 0) + 1
    graph = layered_layout(strata, relations, counts)

    # 草稿重试时预填表单
    prefill: dict = {}
    draft_id = parse_int(request.args.get("draft"))
    if draft_id is not None:
        for d in drafts:
            if d["id"] == draft_id and d["status"] == "retained":
                prefill = d["payload_json"]
                break

    errors = get_flashed_messages(category_filter=["error"])
    return render_template(
        "unit.html",
        unit=unit,
        strata=strata,
        relations=relations,
        batches=batches,
        drafts=drafts,
        graph=graph,
        prefill=prefill,
        form_errors=errors,
    )


# ---------------------------------------------------------------------------
# 层位
# ---------------------------------------------------------------------------

@bp.route("/units/<int:unit_id>/strata", methods=["POST"])
def create_stratum(unit_id: int):
    unit = get_unit_or_404(unit_id)
    code = request.form.get("code", "").strip()
    name = request.form.get("name", "").strip()
    order = parse_int(request.form.get("excavation_order"))

    if not code:
        flash("层位编号不能为空。", "error")
        return redirect(url_for("main.unit_detail", unit_id=unit_id))
    if order is None or order < 1:
        flash("发掘顺序必须是不小于 1 的整数。", "error")
        return redirect(url_for("main.unit_detail", unit_id=unit_id))

    conn = db.get_db()
    try:
        cur = conn.execute(
            """INSERT INTO strata
               (unit_id, code, name, excavation_order, status, created_at)
               VALUES (?, ?, ?, ?, 'open', ?)""",
            (unit_id, code, name, order, now_iso()),
        )
    except Exception:
        conn.rollback()
        flash(
            f"层位 {code} 登记失败：同一探方内层位编号或发掘顺序重复。",
            "error",
        )
        return redirect(url_for("main.unit_detail", unit_id=unit_id))

    audit("stratum_open", unit_id=unit_id, unit_code=unit["code"],
          entity=code, detail=f"开放层位 {code}，发掘顺序 {order}")
    conn.commit()
    flash(f"层位 {code} 已开放（发掘顺序 {order}）。", "success")
    return redirect(url_for("main.unit_detail", unit_id=unit_id,
                            _anchor="strata"))


@bp.route("/units/<int:unit_id>/strata/<int:stratum_id>/close",
          methods=["POST"])
def close_stratum(unit_id: int, stratum_id: int):
    unit = get_unit_or_404(unit_id)
    conn = db.get_db()
    s = conn.execute(
        "SELECT * FROM strata WHERE id = ? AND unit_id = ?",
        (stratum_id, unit_id),
    ).fetchone()
    if s is None:
        abort(404)
    if s["status"] == "closed":
        flash(f"层位 {s['code']} 已是关闭状态。", "error")
        return redirect(url_for("main.unit_detail", unit_id=unit_id))

    conn.execute(
        "UPDATE strata SET status = 'closed', closed_at = ? WHERE id = ?",
        (now_iso(), stratum_id),
    )
    audit("stratum_close", unit_id=unit_id, unit_code=unit["code"],
          entity=s["code"], detail=f"关闭层位 {s['code']}，禁止再补录遗物")
    conn.commit()
    flash(f"层位 {s['code']} 已关闭，此后禁止补录遗物。", "success")
    return redirect(url_for("main.unit_detail", unit_id=unit_id, _anchor="strata"))


# ---------------------------------------------------------------------------
# 覆盖关系
# ---------------------------------------------------------------------------

@bp.route("/units/<int:unit_id>/relations", methods=["POST"])
def create_relation(unit_id: int):
    unit = get_unit_or_404(unit_id)
    upper_id = parse_int(request.form.get("upper_id"))
    lower_id = parse_int(request.form.get("lower_id"))

    anchor = url_for("main.unit_detail", unit_id=unit_id, _anchor="relations")
    if upper_id is None or lower_id is None:
        flash("请选择覆盖方与被覆盖方层位。", "error")
        return redirect(anchor)

    conn = db.get_db()
    strata_rows = fetch_strata(unit_id)
    strata_map = {s["id"]: dict(s) for s in strata_rows}
    relations = fetch_relations(unit_id)

    if upper_id not in strata_map or lower_id not in strata_map:
        flash("所选层位不属于该探方，关系不予登记。", "error")
        return redirect(anchor)

    result = validate_relation(strata_map, relations, upper_id, lower_id)
    upper, lower = strata_map[upper_id], strata_map[lower_id]

    if not result["ok"]:
        code = result["reason_code"]
        path = result["conflict_path"]
        path_text = path_label(path, strata_map)

        if code == "duplicate":
            flash(
                f"覆盖关系 {upper['code']} → {lower['code']} 已存在，"
                "无需重复登记。",
                "error",
            )
            conn.rollback()
            return redirect(anchor)

        if code == "order_violation":
            reason = (
                f"发掘顺序矛盾：{upper['code']}（顺序 {upper['excavation_order']}）"
                f"不可能覆盖 {lower['code']}（顺序 "
                f"{lower['excavation_order']}）。覆盖方应更晚发掘、序号更大。"
            )
        else:  # cycle
            reason = (
                f"新增关系会使层位图成环，违反“覆盖/被覆盖”的单向关系。"
                f"冲突路径：{path_text}（首尾相接构成回路）。"
            )

        payload = dumps({"upper_id": upper_id, "lower_id": lower_id})
        conn.execute(
            """INSERT INTO drafts
               (kind, unit_id, payload, reason, conflict_path, created_at, status)
               VALUES ('relation', ?, ?, ?, ?, ?, 'retained')""",
            (unit_id, payload, reason, path_text, now_iso()),
        )
        audit("relation_reject", result="conflict", unit_id=unit_id,
              unit_code=unit["code"],
              entity=f"{upper['code']} → {lower['code']}", detail=reason,
              conflict_path=path_text)
        conn.commit()
        flash(reason + " 表单已保留为草稿，可在调整层位关系后补录。", "conflict")
        return redirect(anchor)

    conn.execute(
        "INSERT INTO relations (unit_id, upper_id, lower_id, created_at)"
        " VALUES (?, ?, ?, ?)",
        (unit_id, upper_id, lower_id, now_iso()),
    )
    detail = (
        f"登记覆盖关系 {upper['code']}（顺序 {upper['excavation_order']}）"
        f"覆盖 {lower['code']}（顺序 {lower['excavation_order']}）"
    )
    audit("relation_add", unit_id=unit_id, unit_code=unit["code"],
          entity=f"{upper['code']} → {lower['code']}", detail=detail)
    resolve_matching_drafts(
        "relation", unit_id,
        {"upper_id": upper_id, "lower_id": lower_id}, unit["code"],
    )
    conn.commit()
    flash(detail + "。", "success")
    return redirect(anchor)


# ---------------------------------------------------------------------------
# 遗物批次
# ---------------------------------------------------------------------------

@bp.route("/units/<int:unit_id>/batches", methods=["POST"])
def create_batch(unit_id: int):
    unit = get_unit_or_404(unit_id)
    stratum_id = parse_int(request.form.get("stratum_id"))
    code = request.form.get("code", "").strip()
    description = request.form.get("description", "").strip()

    anchor = url_for("main.unit_detail", unit_id=unit_id, _anchor="artifacts")
    if not code:
        flash("遗物批次编号不能为空。", "error")
        return redirect(anchor)
    if stratum_id is None:
        flash("请选择归属层位。", "error")
        return redirect(anchor)

    conn = db.get_db()
    s = conn.execute(
        "SELECT * FROM strata WHERE id = ? AND unit_id = ?",
        (stratum_id, unit_id),
    ).fetchone()
    if s is None:
        flash("归属层位不存在或不属于该探方。", "error")
        return redirect(anchor)

    payload = dumps({
        "stratum_id": stratum_id,
        "code": code,
        "description": description,
    })

    if s["status"] == "closed":
        reason = (
            f"层位 {s['code']} 已关闭（{s['closed_at']}），"
            "已登记层位关闭后禁止补录遗物。"
        )
        conn.execute(
            """INSERT INTO drafts
               (kind, unit_id, payload, reason, conflict_path, created_at, status)
               VALUES ('artifact', ?, ?, ?, '', ?, 'retained')""",
            (unit_id, payload, reason, now_iso()),
        )
        audit("artifact_reject", result="conflict", unit_id=unit_id,
              unit_code=unit["code"], entity=code, detail=reason)
        conn.commit()
        flash(reason + " 该批遗物已保留为草稿。", "conflict")
        return redirect(anchor)

    try:
        conn.execute(
            """INSERT INTO batches
               (unit_id, stratum_id, code, description, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (unit_id, stratum_id, code, description, now_iso()),
        )
    except Exception:
        conn.rollback()
        flash(f"遗物批次编号 {code} 在该探方内已存在。", "error")
        return redirect(anchor)

    audit("artifact_register", unit_id=unit_id, unit_code=unit["code"],
          entity=code, detail=f"向开放层位 {s['code']} 登记遗物批次 {code}")
    resolve_matching_drafts(
        "artifact", unit_id,
        {"stratum_id": stratum_id, "code": code, "description": description},
        unit["code"],
    )
    conn.commit()
    flash(f"遗物批次 {code} 已归入开放层位 {s['code']}。", "success")
    return redirect(anchor)


# ---------------------------------------------------------------------------
# 草稿处理
# ---------------------------------------------------------------------------

@bp.route("/drafts/<int:draft_id>/discard", methods=["POST"])
def discard_draft(draft_id: int):
    conn = db.get_db()
    d = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if d is None:
        abort(404)
    conn.execute(
        "UPDATE drafts SET status = 'discarded', resolved_at = ? WHERE id = ?",
        (now_iso(), draft_id),
    )
    unit = conn.execute("SELECT code FROM units WHERE id = ?",
                        (d["unit_id"],)).fetchone()
    kind_label = "覆盖关系" if d["kind"] == "relation" else "遗物批次"
    audit("draft_discard", result="info", unit_id=d["unit_id"],
          unit_code=unit["code"] if unit else "", entity=f"草稿 #{draft_id}",
          detail=f"丢弃{kind_label}草稿 #{draft_id}")
    conn.commit()
    flash(f"草稿 #{draft_id} 已丢弃（审计中仍可查）。", "success")
    return redirect(url_for("main.unit_detail", unit_id=d["unit_id"],
                            _anchor="drafts"))


# ---------------------------------------------------------------------------
# 回放
# ---------------------------------------------------------------------------

@bp.route("/units/<int:unit_id>/replay")
def replay(unit_id: int):
    unit = get_unit_or_404(unit_id)
    strata = [dict(s) for s in fetch_strata(unit_id)]
    relations = [dict(r) for r in fetch_relations(unit_id)]
    batches = [dict(b) for b in fetch_batches(unit_id)]
    drafts_rows = db.get_db().execute(
        """SELECT * FROM drafts
           WHERE unit_id = ? AND status = 'retained' ORDER BY id""",
        (unit_id,),
    ).fetchall()
    drafts = [
        {"id": d["id"], "created_at": d["created_at"], "reason": d["reason"],
         "conflict_path": d["conflict_path"]}
        for d in drafts_rows
    ]
    frames = build_frames(strata, relations, batches, drafts)
    return render_template("replay.html", unit=unit, frames=frames,
                           frames_json=json.dumps(frames, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------

def query_audit() -> list[dict]:
    sql = "SELECT * FROM audit_log WHERE 1=1"
    args: list = []
    unit_id = parse_int(request.args.get("unit_id"))
    result = request.args.get("result", "")
    if unit_id is not None:
        sql += " AND unit_id = ?"
        args.append(unit_id)
    if result in ("success", "conflict", "info"):
        sql += " AND result = ?"
        args.append(result)
    sql += " ORDER BY ts DESC, id DESC LIMIT 1000"
    return db.get_db().execute(sql, args).fetchall()


@bp.route("/audit")
def audit_page():
    units = db.get_db().execute("SELECT id, code FROM units ORDER BY code").fetchall()
    logs = query_audit()
    return render_template("audit.html", logs=logs, units=units,
                           actions=ACTIONS,
                           f_unit=request.args.get("unit_id", ""),
                           f_result=request.args.get("result", ""))


@bp.route("/audit/export")
def audit_export():
    fmt = request.args.get("format", "csv")
    unit_id = parse_int(request.args.get("unit_id"))
    logs = query_audit()
    audit("export_audit", result="info",
          unit_id=unit_id, unit_code=request.args.get("unit_code", ""),
          entity="audit_log",
          detail=f"导出 {len(logs)} 条审计记录（{fmt.upper()}）")
    db.get_db().commit()

    columns = ["时间(UTC)", "探方", "操作员", "动作", "结果", "对象", "明细",
               "冲突路径"]

    def cells(log: dict) -> list:
        return [
            log["ts"], log["unit_code"], log["actor"],
            ACTIONS.get(log["action"], log["action"]),
            {"success": "成功", "conflict": "冲突", "info": "信息"}[log["result"]],
            log["entity"], log["detail"], log["conflict_path"],
        ]

    if fmt == "json":
        payload = json.dumps(
            [dict(zip(columns, cells(l))) for l in logs],
            ensure_ascii=False, indent=2,
        )
        return Response(
            payload,
            mimetype="application/json; charset=utf-8",
            headers={"Content-Disposition":
                     "attachment; filename=audit_log.json"},
        )

    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM，便于 Excel 正确识别中文
    writer = csv.writer(buf)
    writer.writerow(columns)
    for log in logs:
        writer.writerow(cells(log))
    return Response(
        buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 "attachment; filename=audit_log.csv"},
    )


# ---------------------------------------------------------------------------
# 操作员
# ---------------------------------------------------------------------------

@bp.route("/actor", methods=["POST"])
def set_actor():
    actor = request.form.get("actor", "").strip() or "匿名"
    session["actor"] = actor
    audit("actor_set", result="info", entity=actor,
          detail=f"操作员标识设为 {actor}", actor=actor)
    db.get_db().commit()
    flash(f"操作员已设为：{actor}", "success")
    return redirect(request.referrer or url_for("main.index"))
