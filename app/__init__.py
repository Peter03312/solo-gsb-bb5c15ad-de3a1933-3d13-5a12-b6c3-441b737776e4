"""考古发掘层位记录应用包。"""
from __future__ import annotations

import os
from datetime import datetime

from flask import Flask

from . import db


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    default_db = os.path.join(app.instance_path, "archaeo.db")
    app.config.update(
        DATABASE=os.environ.get("ARCH_DB_PATH", default_db),
        SECRET_KEY=os.environ.get("ARCH_SECRET_KEY", "dev-insecure-secret"),
    )
    if test_config:
        app.config.update(test_config)

    try:
        os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
    except OSError:
        pass

    db.init_app(app)

    from .routes import bp as main_bp

    app.register_blueprint(main_bp)

    @app.template_filter("dt")
    def _format_dt(value: str | None) -> str:
        """将库内 UTC ISO 时间渲染为本地可读时间。"""
        if not value:
            return "—"
        try:
            ts = datetime.fromisoformat(value)
        except ValueError:
            return value
        return ts.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    return app
