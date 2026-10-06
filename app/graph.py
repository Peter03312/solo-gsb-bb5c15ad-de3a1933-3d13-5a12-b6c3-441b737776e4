"""层位图（Harris 式覆盖关系）的图算法。

边 upper -> lower 表示“上层覆盖下层”。
层位图必须保持为有向无环图（DAG），且边方向须与发掘顺序一致：
上层（晚发掘、序号大）覆盖下层（早发掘、序号小）。
"""
from __future__ import annotations

from collections import deque
from typing import Iterable, Sequence

# strata: 以 stratum id 为键的字典，至少含 excavation_order
Adjacency = dict[int, set[int]]


def build_adjacency(
    relations: Iterable[object], candidate: tuple[int, int] | None = None
) -> Adjacency:
    """由关系行（含 upper_id/lower_id）构造邻接表，可附一条候选边。"""
    adj: Adjacency = {}
    for rel in relations:
        upper = rel["upper_id"]
        lower = rel["lower_id"]
        adj.setdefault(upper, set()).add(lower)
        adj.setdefault(lower, set())
    if candidate is not None:
        upper, lower = candidate
        adj.setdefault(upper, set()).add(lower)
        adj.setdefault(lower, set())
    return adj


def find_path(adj: Adjacency, src: int, dst: int) -> list[int]:
    """BFS 返回 src -> dst 的最短路径；不可达返回 []。"""
    if src not in adj or src == dst:
        return [src] if src == dst and src in adj else []
    visited = {src}
    parent: dict[int, int] = {}
    queue: deque[int] = deque([src])
    while queue:
        node = queue.popleft()
        if node == dst:
            path = [node]
            while path[-1] != src:
                path.append(parent[path[-1]])
            path.reverse()
            return path
        for nxt in sorted(adj.get(node, ())):
            if nxt not in visited:
                visited.add(nxt)
                parent[nxt] = node
                queue.append(nxt)
    return []


def find_cycle(adj: Adjacency, edge: tuple[int, int] | None = None) -> list[int]:
    """检测环；edge 为触发边时，返回包含该边的环（不含再次重复的起点）。

    对于候选边 upper->lower，仅当 lower 已能到达 upper 时才成环，
    此时环 = [upper, lower, ..., upper]。
    """
    if edge is not None:
        upper, lower = edge
        path = find_path(adj, lower, upper)
        if path:
            return [upper, *path]
        return []

    color: dict[int, int] = {}  # 0 白 / 1 灰 / 2 黑
    stack: list[int] = []

    def dfs(node: int) -> list[int]:
        color[node] = 1
        stack.append(node)
        for nxt in sorted(adj.get(node, ())):
            if color.get(nxt, 0) == 1:
                idx = stack.index(nxt)
                return stack[idx:]
            if color.get(nxt, 0) == 0:
                found = dfs(nxt)
                if found:
                    return found
        stack.pop()
        color[node] = 2
        return []

    for node in sorted(adj):
        if color.get(node, 0) == 0:
            found = dfs(node)
            if found:
                return found
    return []


def validate_relation(
    strata: dict[int, dict],
    relations: Sequence[dict],
    upper_id: int,
    lower_id: int,
) -> dict:
    """校验新增覆盖关系。

    返回 {ok, reason_code, conflict_path}：
      ok              是否通过
      reason_code     same_stratum / not_found / cross_unit /
                      duplicate / cycle / order_violation / None
      conflict_path   矛盾涉及的层位 id 路径
    """
    upper = strata.get(upper_id)
    lower = strata.get(lower_id)
    if upper_id == lower_id or upper is None or lower is None:
        return {"ok": False, "reason_code": "invalid", "conflict_path": []}

    if upper["unit_id"] != lower["unit_id"]:
        return {"ok": False, "reason_code": "cross_unit", "conflict_path": []}

    for rel in relations:
        if rel["upper_id"] == upper_id and rel["lower_id"] == lower_id:
            return {
                "ok": False,
                "reason_code": "duplicate",
                "conflict_path": [upper_id, lower_id],
            }

    adj = build_adjacency(relations, candidate=(upper_id, lower_id))

    cycle = find_cycle(adj, edge=(upper_id, lower_id))
    if cycle:
        return {"ok": False, "reason_code": "cycle", "conflict_path": cycle}

    # 发掘顺序约束：上层（覆盖方）的发掘序号必须更大，即更晚发掘。
    if upper["excavation_order"] <= lower["excavation_order"]:
        return {
            "ok": False,
            "reason_code": "order_violation",
            "conflict_path": [upper_id, lower_id],
        }

    return {"ok": True, "reason_code": None, "conflict_path": []}
