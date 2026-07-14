#!/usr/bin/env python3
"""Operator CLI — mint, list, and revoke household portal links (finding M2).

Examples:
  DATABASE_URL=postgres://… python3 scripts/mint_link.py --label wife --days 365 \
      --base-url https://family-expenses-xyz.a.run.app
  python3 scripts/mint_link.py --list
  python3 scripts/mint_link.py --revoke <token-or-id>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database  # noqa: E402
from app.store import Store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", help="who this link is for (e.g. 'wife')")
    parser.add_argument("--days", type=int, default=None,
                        help="expiry in days (default: never expires)")
    parser.add_argument("--base-url", default="", help="service URL to print a full link")
    parser.add_argument("--list", action="store_true", help="list existing tokens")
    parser.add_argument("--revoke", metavar="TOKEN_OR_ID", help="revoke a token")
    args = parser.parse_args()

    store = Store(Database())
    store.db.init()

    if args.list:
        for row in store.list_tokens():
            state = ("REVOKED" if row["revoked"]
                     else f"expires {row['expires_at']}" if row["expires_at"]
                     else "never expires")
            print(f"{row['id']}  {row['label'] or '-':12s}  {state}  "
                  f"used {row['use_count']}x (last {row['last_used_at'] or 'never'})")
        return 0

    if args.revoke:
        ok = store.revoke_token(args.revoke)
        print("revoked" if ok else "not found")
        return 0 if ok else 1

    minted = store.mint_token(label=args.label, expires_days=args.days)
    print(f"token:      {minted['token']}")
    print(f"expires_at: {minted['expires_at'] or 'never'}")
    if args.base_url:
        print(f"link:       {args.base_url.rstrip('/')}/t/{minted['token']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
