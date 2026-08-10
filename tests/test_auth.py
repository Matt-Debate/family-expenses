"""Portal OAuth tests — the feature flag, the allowlist, and the guards.

The Auth0 round trip itself is not exercised here (it needs a live tenant); the
live gate is scripts/smoke_live.py. What IS pinned is everything that decides
whether a request is let through, plus the contract that matters most: with the
env vars unset, the portal behaves exactly as it did before OAuth existed.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starlette.testclient import TestClient  # noqa: E402

from app import auth  # noqa: E402
from app.db import Database  # noqa: E402
from app.store import Store  # noqa: E402
from app.web import build_app, build_routes  # noqa: E402

ENABLED_ENV = {
    "AUTH0_DOMAIN": "work-os.jp.auth0.com",
    "AUTH0_CLIENT_ID": "test-client-id",
    "AUTH0_CLIENT_SECRET": "test-client-secret",
    "SESSION_SECRET": "test-session-secret-not-a-real-one",
    "PORTAL_ALLOWED_EMAILS": "her@example.com, Him@Example.com",
}


def make_store() -> tuple[Store, str]:
    db = Database("sqlite:///:memory:")
    db.init()
    store = Store(db)
    return store, store.mint_token(label="wife")["token"]


class FeatureFlagTests(unittest.TestCase):
    def test_disabled_when_nothing_configured(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(auth.is_enabled())

    def test_enabled_only_when_every_required_var_present(self):
        with patch.dict("os.environ", ENABLED_ENV, clear=True):
            self.assertTrue(auth.is_enabled())

    def test_partial_config_fails_closed(self):
        """A half-finished setup must not half-enable auth."""
        for missing in ("AUTH0_DOMAIN", "AUTH0_CLIENT_ID",
                        "AUTH0_CLIENT_SECRET", "SESSION_SECRET"):
            env = dict(ENABLED_ENV)
            env.pop(missing)
            with patch.dict("os.environ", env, clear=True):
                self.assertFalse(auth.is_enabled(), f"enabled without {missing}")

    def test_blank_value_counts_as_missing(self):
        env = dict(ENABLED_ENV, SESSION_SECRET="   ")
        with patch.dict("os.environ", env, clear=True):
            self.assertFalse(auth.is_enabled())


class AllowlistTests(unittest.TestCase):
    def test_allowlist_is_case_insensitive_and_trimmed(self):
        with patch.dict("os.environ", ENABLED_ENV, clear=True):
            self.assertTrue(auth.is_allowed("her@example.com"))
            self.assertTrue(auth.is_allowed("  HER@EXAMPLE.COM  "))
            self.assertTrue(auth.is_allowed("him@example.com"))

    def test_unknown_email_denied(self):
        with patch.dict("os.environ", ENABLED_ENV, clear=True):
            self.assertFalse(auth.is_allowed("stranger@example.com"))

    def test_empty_allowlist_denies_everyone(self):
        """Fails closed. Auth0 will authenticate anyone who signs up, so an
        unset allowlist must not mean 'let them all in'."""
        env = dict(ENABLED_ENV, PORTAL_ALLOWED_EMAILS="")
        with patch.dict("os.environ", env, clear=True):
            self.assertFalse(auth.is_allowed("her@example.com"))

    def test_missing_email_denied(self):
        with patch.dict("os.environ", ENABLED_ENV, clear=True):
            self.assertFalse(auth.is_allowed(None))
            self.assertFalse(auth.is_allowed(""))


class RouteShapeTests(unittest.TestCase):
    def test_auth_routes_absent_when_disabled(self):
        store, _ = make_store()
        with patch.dict("os.environ", {}, clear=True):
            paths = {r.path for r in build_routes(store)}
        self.assertNotIn("/login", paths)
        self.assertNotIn("/callback", paths)
        self.assertNotIn("/logout", paths)

    def test_auth_routes_present_when_enabled(self):
        store, _ = make_store()
        with patch.dict("os.environ", ENABLED_ENV, clear=True):
            paths = {r.path for r in build_routes(store)}
        self.assertIn("/login", paths)
        self.assertIn("/callback", paths)
        self.assertIn("/logout", paths)

    def test_portal_and_api_paths_unchanged_by_oauth(self):
        """A8: adding OAuth must not move the paths her bookmark depends on."""
        store, _ = make_store()
        with patch.dict("os.environ", ENABLED_ENV, clear=True):
            paths = {r.path for r in build_routes(store)}
        for frozen in ("/t/{token}", "/health", "/api/list", "/api/submit"):
            self.assertIn(frozen, paths)


class GuardTests(unittest.TestCase):
    """With OAuth on, an unauthenticated caller gets bounced — not served."""

    def test_portal_redirects_to_login_when_signed_out(self):
        store, token = make_store()
        with patch.dict("os.environ", ENABLED_ENV, clear=True):
            client = TestClient(build_app(store))
            r = client.get(f"/t/{token}", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["location"])
        # carries where she was headed, so login returns her to the ledger
        self.assertIn(f"next=/t/{token}", r.headers["location"])

    def test_invalid_token_still_404s_without_a_login_detour(self):
        """A bad link should fail immediately, not after a pointless round trip."""
        store, _ = make_store()
        with patch.dict("os.environ", ENABLED_ENV, clear=True):
            client = TestClient(build_app(store))
            r = client.get("/t/not-a-real-token", follow_redirects=False)
        self.assertEqual(r.status_code, 404)

    def test_api_returns_401_when_signed_out(self):
        store, token = make_store()
        with patch.dict("os.environ", ENABLED_ENV, clear=True):
            client = TestClient(build_app(store))
            r = client.post("/api/list", json={"token": token})
        self.assertEqual(r.status_code, 401)
        self.assertFalse(r.json()["ok"])

    def test_everything_still_open_when_oauth_is_off(self):
        """The compatibility guarantee: unset env == pre-OAuth behavior."""
        store, token = make_store()
        with patch.dict("os.environ", {}, clear=True):
            client = TestClient(build_app(store))
            self.assertEqual(client.get(f"/t/{token}").status_code, 200)
            self.assertEqual(
                client.post("/api/list", json={"token": token}).status_code, 200
            )


class OpenRedirectTests(unittest.TestCase):
    def test_safe_next_rejects_offsite_targets(self):
        from app.web import _safe_next

        for hostile in ("//evil.com", "https://evil.com", "http://evil.com"):
            self.assertEqual(_safe_next(hostile), "/", hostile)

    def test_safe_next_keeps_same_site_paths(self):
        from app.web import _safe_next

        self.assertEqual(_safe_next("/t/abc123"), "/t/abc123")


if __name__ == "__main__":
    unittest.main()
