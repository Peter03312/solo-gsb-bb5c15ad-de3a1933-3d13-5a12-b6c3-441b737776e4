"""按探方回放层位演变：从业务表推导带布局快照的时间帧。"""
from __future__ import annotations

from .layout import layered_layout

EVENT_LABELS = {
    "stratum_open": "开放层位",
    "relation_add": "登记覆盖关系",
    "batch_register": "登记遗物批次",
    "stratum_close": "关闭层位",
    "draft": "发现矛盾 · 保留草稿",
}


def build_frames(strata: list[dict], relations: list[dict],
                 batches: list[dict], drafts: list[dict]) -> list[dict]:
    """生成逐事件帧。每帧含截至该时刻的层位图快照与说明。

    事件排序：(时间戳, 类型优先级, 实体 id)，与审计日志写入次序一致。
    """
    events: list[tuple] = []
    for s in strata:
        events.append((s["created_at"], 1, s["id"], {
            "type": "stratum_open",
            "stratum_id": s["id"],
            "ts": s["created_at"],
            "desc": f"开放层位 {s['code']}（发掘顺序 {s['excavation_order']}）",
        }))
        if s.get("closed_at"):
            events.append((s["closed_at"], 4, s["id"], {
                "type": "stratum_close",
                "stratum_id": s["id"],
                "ts": s["closed_at"],
                "desc": f"关闭层位 {s['code']}，此后禁止补录遗物",
            }))
    for rel in relations:
        events.append((rel["created_at"], 2, rel["id"], {
            "type": "relation_add",
            "upper_id": rel["upper_id"],
            "lower_id": rel["lower_id"],
            "ts": rel["created_at"],
            "desc": f"建立覆盖关系（{rel['upper_code']} → {rel['lower_code']}）",
        }))
    for b in batches:
        events.append((b["created_at"], 3, b["id"], {
            "type": "batch_register",
            "stratum_id": b["stratum_id"],
            "ts": b["created_at"],
            "desc": f"向层位 {b['stratum_code']} 登记遗物批次 {b['code']}",
        }))
    for d in drafts:
        events.append((d["created_at"], 5, d["id"], {
            "type": "draft",
            "draft_id": d["id"],
            "ts": d["created_at"],
            "desc": d["reason"],
            "conflict_path": d.get("conflict_path", ""),
        }))

    events.sort(key=lambda e: (e[0], e[1], e[2]))

    frames: list[dict] = []
    cur_strata: dict[int, dict] = {}
    cur_relations: list[dict] = []
    cur_counts: dict[int, int] = {}
    highlights: set[int] = set()
    highlight_edge: tuple[int, int] | None = None
    notice: dict | None = None

    for _, _, _, ev in events:
        t = ev["type"]
        highlights = set()
        highlight_edge = None
        notice = None

        if t == "stratum_open":
            s = next(x for x in strata if x["id"] == ev["stratum_id"])
            cur_strata[s["id"]] = dict(s)
            highlights.add(s["id"])
        elif t == "stratum_close":
            cur_strata[ev["stratum_id"]]["status"] = "closed"
            highlights.add(ev["stratum_id"])
        elif t == "relation_add":
            cur_relations.append(
                {"upper_id": ev["upper_id"], "lower_id": ev["lower_id"]}
            )
            highlights.update({ev["upper_id"], ev["lower_id"]})
            highlight_edge = (ev["upper_id"], ev["lower_id"])
        elif t == "batch_register":
            cur_counts[ev["stratum_id"]] = cur_counts.get(ev["stratum_id"], 0) + 1
            highlights.add(ev["stratum_id"])
        elif t == "draft":
            notice = {
                "kind": "conflict",
                "desc": ev["desc"],
                "conflict_path": ev.get("conflict_path", ""),
            }

        layout = layered_layout(cur_strata.values(), cur_relations, cur_counts)
        for node in layout["nodes"]:
            node["highlight"] = node["id"] in highlights
        for edge in layout["edges"]:
            edge["highlight"] = highlight_edge == (edge["upper_id"], edge["lower_id"])

        frames.append({
            "type": t,
            "type_label": EVENT_LABELS[t],
            "ts": ev["ts"],
            "desc": ev["desc"],
            "notice": notice,
            "stratum_count": len(cur_strata),
            "relation_count": len(cur_relations),
            "batch_count": sum(cur_counts.values()),
            "layout": layout,
        })

    return frames
