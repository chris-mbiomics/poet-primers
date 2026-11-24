"""Flask web interface for POET."""

from __future__ import annotations

import os
from typing import Any

from flask import Flask, render_template

from .. import __version__
from .api import api

#: Upper limit on submitted sequence length (bases).
MAX_SEQUENCE_LENGTH = 500_000


def create_app(config: dict[str, Any] | None = None) -> Flask:
    """Application factory."""
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        MAX_SEQUENCE_LENGTH=MAX_SEQUENCE_LENGTH,
        MAX_PAIRS=2000,
        DEGEPRIME_WORKERS=min(8, os.cpu_count() or 1),
        JSON_SORT_KEYS=False,
    )
    if config:
        app.config.update(config)

    app.register_blueprint(api, url_prefix="/api")

    @app.get("/")
    def index() -> str:
        return render_template("index.html", version=__version__)

    return app
