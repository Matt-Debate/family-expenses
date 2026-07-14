#!/usr/bin/env python3
"""Post-deploy live smoke — run from your PC right after `gcloud run deploy`.

Proves the two things the sqlite test suite cannot:
  1. db/schema.sql applies cleanly to the real Neon Postgres;
  2. the deployed service works end-to-end over the public URL.

Usage:
  DATABASE_URL='postgres://…' python3 scripts/smoke_live.py \
      --base-url https://family-expenses-xxxx.a.run.app

Creates a temporary link + expense, exercises the full flow, then deletes the
expense and revokes the link. Exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402
from app.store import Store  # noqa: E402

FAILED = False


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILED
    print(f"  {'✓' if ok else '✗ FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED = True


def get(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.status, r.read().decode()


def post(base: str, name: str, **body):
    req = urllib.request.Request(
        f"{base}/api/{name}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="deployed service URL")
    base = parser.parse_args().base_url.rstrip("/")

    print("1. Postgres schema (direct via DATABASE_URL)")
    store = Store(Database())  # requires DATABASE_URL env
    if not store.db.is_pg:
        print("  ✗ DATABASE_URL is not set to a postgres:// URL"); return 1
    store.db.init()  # idempotent — proves the portable DDL on real PG
    check("schema.sql applied idempotently to Postgres", True)

    minted = store.mint_token(label="smoke-test")
    token = minted["token"]
    check("temporary smoke link minted", len(token) == 64)

    eid = None
    try:
        print("2. Deployed service over the public URL")
        status, _ = get(f"{base}/healthz")
        check("GET /healthz", status == 200)

        status, html = get(f"{base}/t/{token}")
        check("portal page serves for valid token", status == 200 and "家庭开支" in html)

        try:
            get(f"{base}/t/definitely-wrong-token")
            check("bad token rejected", False, "expected 404")
        except urllib.error.HTTPError as exc:
            check("bad token rejected (404)", exc.code == 404)

        print("3. Full expense flow (temporary data, cleaned up below)")
        out = post(base, "submit", token=token, date="2026-01-01",
                   amount=1.23, description="[smoke] delete me", submitted_by="smoke")
        eid = out["expense"]["id"]
        check("submit", out["ok"])

        out = post(base, "list", token=token, status="unpaid")
        check("list shows it", any(e["id"] == eid for e in out["expenses"]))

        out = post(base, "mark-paid", token=token, id=eid,
                   paid=True, paid_date="2026-01-02", changed_by="smoke")
        check("mark paid", out["expense"]["paid"])

        out = post(base, "history", token=token, id=eid)
        actions = [h["action"] for h in out["history"]]
        check("history atomic trail", actions == ["create", "mark_paid"], str(actions))

        out = post(base, "delete", token=token, id=eid, changed_by="smoke")
        check("delete (cleanup)", out["ok"])
        eid = None

        status, _ = get(f"{base}/healthz")  # still alive after the workout
        check("service healthy after flow", status == 200)
    finally:
        print("4. Cleanup")
        if eid:
            try:
                post(base, "delete", token=token, id=eid, changed_by="smoke")
            except Exception:
                print("  ! could not delete smoke expense — remove '[smoke]' row manually")
        check("smoke link revoked", store.revoke_token(token))

    print("\nRESULT:", "FAIL — see ✗ above" if FAILED else
          "PASS — deployment verified end-to-end. Mint the real link next "
          "(scripts/mint_link.py --label wife --base-url " + base + ")")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
