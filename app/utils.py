"""通用工具：时间与 JSON 辅助。"""
from __future__ import annotations

import json
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    """审计/事件排序用，微秒精度 UTC ISO 字符串。"""
    return utcnow().isoformat(timespec="microseconds")


def dumps(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def loads(text: str) -> object:
    return json.loads(text)
