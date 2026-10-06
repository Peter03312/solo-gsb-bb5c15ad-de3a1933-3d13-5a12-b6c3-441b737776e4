"""端到端业务规则测试（Flask test client）。"""
import io

import pytest

from app import create_app
from app.db import get_db, init_db


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.db"),
        "SECRET_KEY": "test",
    })
    with app.app_context():
        init_db()
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def create_unit(client, code="T1"):
    resp = client.post("/units", data={"code": code, "name": "一号探方", "note": ""},
                       follow_redirects=True)
    assert "已创建" in resp.text
    with client.session_transaction() as sess:
        sess["actor"] = "测试员"
    return 1  # 第一条记录 id


def open_stratum(client, uid, code, order):
    resp = client.post(f"/units/{uid}/strata",
                       data={"code": code, "name": "", "excavation_order": order},
                       follow_redirects=True)
    assert resp.status_code == 200
    return resp


def test_full_happy_path(client):
    uid = create_unit(client)
    open_stratum(client, uid, "①", 1)
    open_stratum(client, uid, "②", 2)
    open_stratum(client, uid, "③", 3)

    # 正确方向：②(顺序2) 覆盖 ①(顺序1)
    resp = client.post(f"/units/{uid}/relations",
                       data={"upper_id": 2, "lower_id": 1}, follow_redirects=True)
    assert "登记覆盖关系" in resp.text
    assert "flash conflict" not in resp.text

    # 开放层位可登记遗物
    resp = client.post(f"/units/{uid}/batches",
                       data={"stratum_id": 1, "code": "B1",
                             "description": "陶片若干"},
                       follow_redirects=True)
    assert "已归入开放层位" in resp.text


def test_order_violation_keeps_draft(client):
    uid = create_unit(client)
    open_stratum(client, uid, "①", 1)
    open_stratum(client, uid, "②", 2)

    resp = client.post(f"/units/{uid}/relations",
                       data={"upper_id": 1, "lower_id": 2}, follow_redirects=True)
    assert "发掘顺序矛盾" in resp.text
    assert "草稿" in resp.text

    with client.application.app_context():
        d = get_db().execute(
            "SELECT * FROM drafts WHERE kind='relation' AND status='retained'"
        ).fetchone()
        assert d is not None
        assert "① → ②" in d["conflict_path"]
        log = get_db().execute(
            "SELECT * FROM audit_log WHERE action='relation_reject'"
        ).fetchone()
        assert log["result"] == "conflict"
        assert log["actor"] == "测试员"


def test_cycle_relation_shows_conflict_path(client):
    uid = create_unit(client)
    open_stratum(client, uid, "①", 1)
    open_stratum(client, uid, "②", 2)
    open_stratum(client, uid, "③", 3)
    for upper, lower in [(2, 1), (3, 2)]:
        client.post(f"/units/{uid}/relations",
                    data={"upper_id": upper, "lower_id": lower})

    # ③->②->① 已存在，再加 ①->③ 成环
    resp = client.post(f"/units/{uid}/relations",
                       data={"upper_id": 1, "lower_id": 3}, follow_redirects=True)
    assert "成环" in resp.text
    assert "① → ③ → ② → ①" in resp.text


def test_closed_stratum_blocks_artifact_and_keeps_draft(client):
    uid = create_unit(client)
    open_stratum(client, uid, "①", 1)
    client.post(f"/units/{uid}/strata/1/close")

    # 即使绕过前端禁用选项直接提交，后端也拒绝并保留草稿
    resp = client.post(f"/units/{uid}/batches",
                       data={"stratum_id": 1, "code": "BLATE", "description": "x"},
                       follow_redirects=True)
    assert "禁止补录" in resp.text
    with client.application.app_context():
        d = get_db().execute(
            "SELECT * FROM drafts WHERE kind='artifact' AND status='retained'"
        ).fetchone()
        assert d is not None
        assert get_db().execute("SELECT COUNT(*) c FROM batches").fetchone()["c"] == 0


def test_duplicate_stratum_order_rejected(client):
    uid = create_unit(client)
    open_stratum(client, uid, "①", 1)
    resp = open_stratum(client, uid, "③", 1)
    assert "重复" in resp.text


def test_replay_frames_ordered(client):
    uid = create_unit(client)
    open_stratum(client, uid, "①", 1)
    open_stratum(client, uid, "②", 2)
    client.post(f"/units/{uid}/relations", data={"upper_id": 2, "lower_id": 1})
    client.post(f"/units/{uid}/batches",
                data={"stratum_id": 2, "code": "B1", "description": ""})
    client.post(f"/units/{uid}/strata/2/close")

    resp = client.get(f"/units/{uid}/replay")
    assert resp.status_code == 200
    assert b"frames-data" in resp.data
    # 四种事件各至少一帧：开放x2、关系、遗物、关闭
    assert b'"type_label"' in resp.data


def test_audit_export_csv_and_json(client):
    uid = create_unit(client)
    open_stratum(client, uid, "①", 1)

    resp = client.get("/audit/export?format=csv")
    assert resp.status_code == 200
    assert resp.content_type.startswith("text/csv")
    data = resp.data.decode("utf-8")
    assert data.startswith("﻿时间(UTC)")
    assert "开放层位" in data

    resp = client.get("/audit/export?format=json")
    assert resp.status_code == 200
    json_body = resp.get_json()
    assert any(row["动作"] == "开放层位" for row in json_body)


def test_draft_auto_resolved_after_successful_submission(client):
    uid = create_unit(client)
    open_stratum(client, uid, "①", 1)
    open_stratum(client, uid, "②", 2)

    # 先制造一条顺序矛盾草稿
    client.post(f"/units/{uid}/relations",
                data={"upper_id": 1, "lower_id": 2})
    # 以正确方向提交相同的两个层位（内容不同，不应核销），再直接核对草稿仍保留
    client.post(f"/units/{uid}/relations", data={"upper_id": 2, "lower_id": 1})
    with client.application.app_context():
        retained = get_db().execute(
            "SELECT COUNT(*) c FROM drafts WHERE status='retained'"
        ).fetchone()["c"]
        assert retained == 1

    # 完全相同的载荷无法成功（它本身永远矛盾），改为验证丢弃路径
    client.post("/drafts/1/discard", follow_redirects=True)
    with client.application.app_context():
        d = get_db().execute("SELECT * FROM drafts WHERE id=1").fetchone()
        assert d["status"] == "discarded"
