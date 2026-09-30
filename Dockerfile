# Family Expenses — single Cloud Run service (portal + API + MCP).
#
# Pattern mirrors the reference work-dashboards Dockerfile.mcp: slim Python
# base, requirements layer first for caching, no frontend build stage needed
# (the portal is one self-contained HTML file served by the app).
#
# Deploy the clean, SHA-pinned image with scripts/deploy.sh. The script binds
# MCP member policy from a pinned Secret Manager version; existing bindings stay.

FROM python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /family-expenses

COPY requirements.lock /tmp/requirements.lock
RUN pip install -r /tmp/requirements.lock

COPY app/ /family-expenses/app/
COPY db/ /family-expenses/db/
COPY scripts/ /family-expenses/scripts/

# Cloud Run injects $PORT (default 8080); app/main.py reads it.
EXPOSE 8080
CMD ["python", "-m", "app.main"]
