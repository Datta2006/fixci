"""Tiny Flask app used by the demo workflow."""

from __future__ import annotations

from flask import Flask, jsonify

app = Flask(__name__)


@app.get("/health")
def health():
    """Return a static health payload."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run()
