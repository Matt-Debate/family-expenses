"""Starlette wiring: portal page + JSON API.

Same ASGI stack the work-dashboards Cloud Run MCP already runs on
(starlette/uvicorn) — no new tech. ``build_app(store)`` is used by tests and
by ``app/main.py`` (which also mounts the MCP under /mcp).
"""

from __future__ import annotations

from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from .api import HANDLERS
from .store import Store

_PORTAL_HTML = (Path(__file__).resolve().parent / "portal.html").read_text(
    encoding="utf-8"
)

_INVALID_LINK_HTML = """<!doctype html><html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>链接无效 / Invalid link</title></head>
<body style="font-family:sans-serif;text-align:center;padding:3rem 1rem">
<h1>链接无效或已过期</h1>
<p>This link is invalid or has expired.</p>
<p>请联系家人获取新链接。/ Please ask for a new link.</p>
</body></html>"""


def build_routes(store: Store) -> list[Route]:
    async def healthz(request: Request):
        return JSONResponse({"ok": True})

    async def favicon(request: Request):
        return Response(status_code=204)

    async def portal(request: Request):
        token = request.path_params["token"]
        if store.validate_token(token) is None:
            return HTMLResponse(_INVALID_LINK_HTML, status_code=404)
        return HTMLResponse(_PORTAL_HTML)

    def make_api_endpoint(handler):
        async def endpoint(request: Request):
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {"ok": False, "error": "invalid JSON body"}, status_code=400
                )
            status, payload = handler(store, body)
            return JSONResponse(payload, status_code=status)

        return endpoint

    routes = [
        Route("/health", healthz, methods=["GET"]),
        Route("/healthz", healthz, methods=["GET"]),
        Route("/favicon.ico", favicon, methods=["GET"]),
        Route("/t/{token}", portal, methods=["GET"]),
    ]
    routes += [
        Route(f"/api/{name}", make_api_endpoint(handler), methods=["POST"])
        for name, handler in HANDLERS.items()
    ]
    return routes


def build_app(store: Store) -> Starlette:
    """Portal-only app (tests / running without the MCP mount)."""
    return Starlette(routes=build_routes(store))
