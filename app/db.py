"""SQLite 连接管理与建表脚本。"""
from __future__ import annotations

import sqlite3

import click
from flask import Flask, current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL DEFAULT '',
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strata (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id         INTEGER NOT NULL REFERENCES units(id),
    code            TEXT NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    excavation_order INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_at      TEXT NOT NULL,
    closed_at       TEXT,
    UNIQUE (unit_id, code),
    UNIQUE (unit_id, excavation_order)
);

CREATE TABLE IF NOT EXISTS batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id     INTEGER NOT NULL REFERENCES units(id),
    stratum_id  INTEGER NOT NULL REFERENCES strata(id),
    code        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE (unit_id, code)
);

CREATE TABLE IF NOT EXISTS relations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id   INTEGER NOT NULL REFERENCES units(id),
    upper_id  INTEGER NOT NULL REFERENCES strata(id),
    lower_id  INTEGER NOT NULL REFERENCES strata(id),
    created_at TEXT NOT NULL,
    UNIQUE (unit_id, upper_id, lower_id),
    CHECK (upper_id <> lower_id)
);

-- 校验失败时保留的草稿（关系草稿 / 遗物草稿）
CREATE TABLE IF NOT EXISTS drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL CHECK (kind IN ('relation', 'artifact')),
    unit_id       INTEGER NOT NULL REFERENCES units(id),
    payload       TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    conflict_path TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'retained'
                  CHECK (status IN ('retained', 'applied', 'discarded')),
    resolved_at   TEXT
);

-- 只增不改的审计日志
CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    unit_id       INTEGER,
    unit_code     TEXT NOT NULL DEFAULT '',
    actor         TEXT NOT NULL DEFAULT '',
    action        TEXT NOT NULL,
    result        TEXT NOT NULL CHECK (result IN ('success', 'conflict', 'info')),
    entity        TEXT NOT NULL DEFAULT '',
    detail        TEXT NOT NULL DEFAULT '',
    conflict_path TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_unit ON audit_log (unit_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log (ts, id);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db() -> None:
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()


def init_app(app: Flask) -> None:
    app.teardown_appcontext(close_db)

    @app.cli.command("init-db")
    def init_db_command() -> None:  # pragma: no cover - 运维入口
        """初始化数据库表结构。"""
        init_db()
        click.echo("数据库已初始化。")

    @app.before_request
    def _ensure_schema() -> None:
        # 多 worker / 新挂载数据卷时幂等兜底
        get_db().executescript(SCHEMA)
