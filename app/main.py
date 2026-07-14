"""Entrypoint — one service: portal + JSON API + MCP mount.

Layout:
  GET  /healthz, /t/<token>, POST /api/*   — household portal (token auth)
  /mcp                                     — operator MCP (bearer-secret auth)

Config (env):
  DATABASE_URL  postgres://… (Neon) — falls back to a local sqlite file
  MCP_SECRET    required for /mcp to serve (fail-closed otherwise)
  PORT, HOST    Cloud Run injects PORT (default 8080)
"""

from __future__ import annotations

import os

from .db import Database
from .mcp_server import McpBearerMiddleware, build_mcp
from .store import Store
from .web import build_routes


def build_asgi_app():
    db = Database()
    db.init()  # idempotent schema apply
    store = Store(db)
    mcp = build_mcp(store)
    # FastMCP's streamable-HTTP app carries the session-manager lifespan;
    # portal routes are appended onto the same app so one service serves both.
    app = mcp.streamable_http_app()
    app.router.routes.extend(build_routes(store))
    return McpBearerMiddleware(app)


def main() -> None:
    import uvicorn

    uvicorn.run(
        build_asgi_app(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
