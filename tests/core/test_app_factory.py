"""``configure_app`` answers ``HEAD`` on every ``GET`` route.

FastAPI does not add ``HEAD`` to a route declared with ``@router.get(...)``, so on
its own every route answers ``HEAD`` with 405. Link checkers and harvesters that
probe a URL before fetching it read that as broken, which matters most on the
``/rdf/`` identifiers. ``_HeadAsGetMiddleware`` handles it once for both services
rather than per route; these tests pin the two properties that matter: no 405,
and headers still describe the ``GET`` response (``content-length`` included)
while the body is empty.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from rfdb_core.app_factory import configure_app


def _build_app() -> FastAPI:
    app = FastAPI()
    configure_app(app, cors_origins=["http://localhost:5173"])

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/stream")
    def stream():
        def gen():
            yield b"chunk1"
            yield b"chunk2"

        return StreamingResponse(gen(), media_type="text/plain")

    @app.post("/write-only")
    def write_only():
        return {"ok": True}

    return app


def test_head_matches_get_headers_with_empty_body() -> None:
    client = TestClient(_build_app())
    get_response = client.get("/health")
    head_response = client.head("/health")

    assert head_response.status_code == get_response.status_code
    assert head_response.headers["content-length"] == get_response.headers["content-length"]
    assert head_response.content == b""


def test_head_drains_a_streaming_response_without_leaking_body() -> None:
    client = TestClient(_build_app())
    head_response = client.head("/stream")

    assert head_response.status_code == 200
    assert head_response.content == b""


def test_head_on_unknown_path_is_still_404() -> None:
    client = TestClient(_build_app())
    assert client.head("/does-not-exist").status_code == 404


def test_head_does_not_widen_a_post_only_route() -> None:
    client = TestClient(_build_app())
    assert client.head("/write-only").status_code == 405


def test_access_log_sees_the_real_head_method(caplog) -> None:
    """The middleware must not leak its internal GET rewrite into the access log."""
    client = TestClient(_build_app())
    with caplog.at_level("INFO", logger="api.access"):
        client.head("/health")

    assert any(r.method == "HEAD" for r in caplog.records if hasattr(r, "method"))
