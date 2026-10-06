"""层位图分层布局（最长路径分层，供 SVG / 回放共用）。"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable

NODE_W = 150
NODE_H = 64
COL_GAP = 60
ROW_GAP = 90
MARGIN = 60


def layered_layout(
    strata: Iterable[dict],
    relations: Iterable[dict],
    artifact_counts: dict[int, int] | None = None,
) -> dict:
    """按最长路径为每个层位计算行列坐标。

    覆盖者（upper）位于上方；同层按编号水平排列并整体垂直居中。
    """
    strata = list(strata)
    relations = list(relations)
    artifact_counts = artifact_counts or {}

    ids = {s["id"] for s in strata}
    adj: dict[int, list[int]] = defaultdict(list)
    indeg: dict[int, int] = {i: 0 for i in ids}
    for rel in relations:
        upper, lower = rel["upper_id"], rel["lower_id"]
        if upper in ids and lower in ids:
            adj[upper].append(lower)
            indeg[lower] += 1

    # Kahn 拓扑 + 最长路径
    queue: deque[int] = deque(sorted(i for i, d in indeg.items() if d == 0))
    topo: list[int] = []
    indeg_work = dict(indeg)
    while queue:
        node = queue.popleft()
        topo.append(node)
        for nxt in sorted(adj[node]):
            indeg_work[nxt] -= 1
            if indeg_work[nxt] == 0:
                queue.append(nxt)
    if len(topo) != len(ids):  # 理论不会发生（写入前已防环），兜底
        topo += [i for i in ids if i not in topo]

    rank: dict[int, int] = {i: 0 for i in ids}
    for node in topo:
        for nxt in adj[node]:
            rank[nxt] = max(rank[nxt], rank[node] + 1)

    by_rank: dict[int, list[int]] = defaultdict(list)
    for s in sorted(strata, key=lambda s: (s["excavation_order"], s["code"])):
        by_rank[rank[s["id"]]].append(s["id"])

    max_cols = max((len(v) for v in by_rank.values()), default=1)
    max_rank = max(by_rank, default=0)
    width = MARGIN * 2 + max_cols * NODE_W + (max_cols - 1) * COL_GAP
    height = MARGIN * 2 + (max_rank + 1) * NODE_H + max_rank * ROW_GAP

    pos: dict[int, tuple[float, float]] = {}
    for r, members in by_rank.items():
        row_w = len(members) * NODE_W + (len(members) - 1) * COL_GAP
        x0 = (width - row_w) / 2
        y = MARGIN + r * (NODE_H + ROW_GAP)
        for col, node_id in enumerate(members):
            pos[node_id] = (x0 + col * (NODE_W + COL_GAP), y)

    by_id = {s["id"]: s for s in strata}
    nodes = []
    for s in strata:
        x, y = pos[s["id"]]
        nodes.append(
            {
                "id": s["id"],
                "code": s["code"],
                "name": s["name"] if "name" in s.keys() else "",
                "excavation_order": s["excavation_order"],
                "status": s["status"],
                "x": x,
                "y": y,
                "w": NODE_W,
                "h": NODE_H,
                "artifact_count": artifact_counts.get(s["id"], 0),
            }
        )

    edges = []
    for rel in relations:
        if rel["upper_id"] in pos and rel["lower_id"] in pos:
            ux, uy = pos[rel["upper_id"]]
            lx, ly = pos[rel["lower_id"]]
            edges.append(
                {
                    "upper_id": rel["upper_id"],
                    "lower_id": rel["lower_id"],
                    # 起止点收到节点框边缘，避免箭头没入矩形
                    "x1": ux + NODE_W / 2,
                    "y1": uy + NODE_H,
                    "x2": lx + NODE_W / 2,
                    "y2": ly,
                }
            )

    return {"nodes": nodes, "edges": edges, "width": width, "height": height}
