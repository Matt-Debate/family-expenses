# Family Expenses — single Cloud Run service (portal + API + MCP).
#
# Pattern mirrors the reference work-dashboards Dockerfile.mcp: slim Python
# base, requirements layer first for caching, no frontend build stage needed
# (the portal is one self-contained HTML file served by the app).
#
# Deploy the clean, SHA-pinned image with scripts/deploy.sh. The script binds
# DATABASE_URL from Secret Manager and deliberately leaves MCP_SECRET unset.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /family-expenses

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

COPY app/ /family-expenses/app/
COPY db/ /family-expenses/db/
COPY scripts/ /family-expenses/scripts/

# Cloud Run injects $PORT (default 8080); app/main.py reads it.
EXPOSE 8080
CMD ["python", "-m", "app.main"]
