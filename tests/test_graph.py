"""图算法单元测试。"""
from app.graph import build_adjacency, find_cycle, find_path, validate_relation


def _rels(pairs):
    return [{"upper_id": u, "lower_id": l} for u, l in pairs]


def _strata(spec):
    # spec: {id: excavation_order}
    return {i: {"id": i, "unit_id": 1, "excavation_order": o} for i, o in spec.items()}


def test_find_path_shortest():
    adj = build_adjacency(_rels([(1, 2), (2, 4), (1, 3), (3, 4)]))
    assert find_path(adj, 1, 4) == [1, 2, 4]
    assert find_path(adj, 4, 1) == []


def test_find_cycle_with_trigger_edge():
    # 已有 1->2->3，候选 3->1 应成环
    adj = build_adjacency(_rels([(1, 2), (2, 3)]), candidate=(3, 1))
    cycle = find_cycle(adj, edge=(3, 1))
    assert cycle == [3, 1, 2, 3]


def test_acyclic_graph_has_no_cycle():
    adj = build_adjacency(_rels([(1, 2), (2, 3)]), candidate=(1, 3))
    assert find_cycle(adj, edge=(1, 3)) == []


def test_validate_order_violation_but_no_cycle():
    # 顺序方向错误，但不会成环
    strata = _strata({1: 1, 2: 2})
    result = validate_relation(strata, _rels([]), upper_id=1, lower_id=2)
    assert result["ok"] is False
    assert result["reason_code"] == "order_violation"
    assert result["conflict_path"] == [1, 2]


def test_validate_cycle_detected_independently_of_order():
    # 顺序符合方向（upper 序号大），但加入后会成环
    strata = _strata({1: 1, 2: 2, 3: 3})
    rels = _rels([(3, 2), (2, 1)])
    result = validate_relation(strata, rels, upper_id=1, lower_id=3)
    assert result["ok"] is False
    assert result["reason_code"] == "cycle"
    assert result["conflict_path"] == [1, 3, 2, 1]


def test_validate_duplicate_and_self_loop():
    strata = _strata({1: 1, 2: 2})
    assert validate_relation(strata, _rels([(2, 1)]), 2, 1)["reason_code"] == "duplicate"
    assert validate_relation(strata, [], 1, 1)["ok"] is False


def test_validate_success():
    strata = _strata({1: 1, 2: 2})
    assert validate_relation(strata, [], 2, 1)["ok"] is True
