"""Web-tier tests — full HTTP path via Starlette TestClient, sqlite-backed."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starlette.testclient import TestClient  # noqa: E402

from app.db import Database  # noqa: E402
from app.store import Store  # noqa: E402
from app.web import build_app  # noqa: E402


def make_client() -> tuple[TestClient, Store, str]:
    db = Database("sqlite:///:memory:")
    db.init()
    store = Store(db)
    token = store.mint_token(label="wife")["token"]
    return TestClient(build_app(store)), store, token


class PortalPageTests(unittest.TestCase):
    def setUp(self):
        self.client, self.store, self.token = make_client()

    def test_healthz(self):
        for path in ("/health", "/healthz"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.json(), {"ok": True})

    def test_favicon_request_is_quiet(self):
        self.assertEqual(self.client.get("/favicon.ico").status_code, 204)

    def test_portal_served_for_valid_token(self):
        r = self.client.get(f"/t/{self.token}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("家庭开支", r.text)

    def test_portal_rejects_bad_token(self):
        r = self.client.get("/t/nope")
        self.assertEqual(r.status_code, 404)
        self.assertIn("链接无效", r.text)

    def test_portal_rejects_revoked_token(self):
        self.store.revoke_token(self.token)
        self.assertEqual(self.client.get(f"/t/{self.token}").status_code, 404)


class ApiAuthTests(unittest.TestCase):
    def setUp(self):
        self.client, self.store, self.token = make_client()

    def test_all_endpoints_reject_missing_token(self):
        for name in ("list", "submit", "update", "mark-paid", "delete", "history"):
            r = self.client.post(f"/api/{name}", json={})
            self.assertEqual(r.status_code, 401, name)
            self.assertFalse(r.json()["ok"])

    def test_invalid_json_is_400(self):
        r = self.client.post(
            "/api/list", content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(r.status_code, 400)


class ApiFlowTests(unittest.TestCase):
    """A1 end-to-end: submit → list → edit → mark paid → history → delete."""

    def setUp(self):
        self.client, self.store, self.token = make_client()

    def post(self, name, **body):
        body["token"] = self.token
        return self.client.post(f"/api/{name}", json=body)

    def test_full_flow(self):
        r = self.post("submit", date="2026-07-14", amount=88.8,
                      description="小提琴课", submitted_by="Wei")
        self.assertEqual(r.status_code, 200)
        eid = r.json()["expense"]["id"]

        r = self.post("list")
        self.assertEqual(r.json()["summary"]["unpaid"], 88.8)
        self.assertEqual(len(r.json()["expenses"]), 1)

        r = self.post("update", id=eid, changed_by="Matt",
                      fields={"amount": 99.9, "category": "kids"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["expense"]["amount"], 99.9)

        r = self.post("mark-paid", id=eid, paid=True,
                      paid_date="2026-07-15", changed_by="Matt")
        self.assertTrue(r.json()["expense"]["paid"])

        r = self.post("list", status="unpaid")
        self.assertEqual(r.json()["expenses"], [])

        r = self.post("history", id=eid)
        self.assertEqual(
            [h["action"] for h in r.json()["history"]],
            ["create", "update", "mark_paid"],
        )

        r = self.post("delete", id=eid, changed_by="Matt")
        self.assertEqual(r.status_code, 200)
        r = self.post("list")
        self.assertEqual(r.json()["expenses"], [])

    def test_validation_maps_to_400(self):
        r = self.post("submit", date="2026-07-14", amount=-1)
        self.assertEqual(r.status_code, 400)
        r = self.post("submit", date="bad", amount=1)
        self.assertEqual(r.status_code, 400)
        r = self.post("mark-paid", id="whatever", paid=True)  # no paid_date
        self.assertEqual(r.status_code, 400)

    def test_missing_id_maps_to_404(self):
        for name, extra in (
            ("update", {"fields": {"amount": 1}}),
            ("mark-paid", {"paid": True, "paid_date": "2026-07-15"}),
            ("delete", {}),
        ):
            r = self.post(name, id="missing", **extra)
            self.assertEqual(r.status_code, 404, name)


class PortalEscapingTests(unittest.TestCase):
    """Regression guard for the stored-XSS fix in 0.4.5.

    render() builds list rows with innerHTML, so every server-supplied value has to
    pass through esc(). `category` is deliberately free-form end-to-end (MCP callers
    pass arbitrary strings and the ledger stores them verbatim), which makes the
    render-time escape the only control standing between a planted category and a
    stolen portal token.
    """

    PORTAL = Path(__file__).resolve().parent.parent / "app" / "portal.html"

    def setUp(self):
        self.html = self.PORTAL.read_text(encoding="utf-8")

    def test_every_use_of_catlabel_is_escaped_or_a_truthiness_guard(self):
        """Account for every `catLabel` occurrence; anything left over is a raw use.

        Deliberately not a `+ catLabel` adjacency check: the original bug read
        `esc(e.description) || catLabel || t("cat")`, where the raw use sits between
        `||` operators and no such pattern matches. Nor is `assertIn("esc(catLabel)")`
        sufficient — the escaped call already existed elsewhere in render() while the
        hole was open. Subtracting the known-safe forms is what actually discriminates.
        """
        offenders = []
        for lineno, line in enumerate(self.html.splitlines(), 1):
            if "catLabel" not in line:
                continue
            residue = line.replace("var catLabel", "")
            residue = residue.replace("esc(catLabel)", "")
            residue = re.sub(r"catLabel\s*\?", "", residue)  # truthiness guard only
            if "catLabel" in residue:
                offenders.append(f"  app/portal.html:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "catLabel reaches innerHTML unescaped — wrap it in esc():\n"
            + "\n".join(offenders),
        )

    def test_category_is_stored_verbatim_not_sanitized_on_write(self):
        """Escaping belongs at render, not on write.

        Mangling stored categories would corrupt the ledger and break MCP round-trips,
        so the API must keep the raw bytes and the portal must escape them.
        """
        client, _store, token = make_client()
        payload = "<img src=x onerror=alert(1)>"
        r = client.post("/api/submit", json={
            "token": token, "date": "2026-07-14", "amount": 5, "category": payload,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["expense"]["category"], payload)


class CategoryParityTests(unittest.TestCase):
    """The portal's category list and app/store.py CATEGORIES must not drift.

    They are two hand-maintained lists in two languages. If the portal offers a
    key analytics does not group by (or vice versa), spending quietly lands in a
    bucket nobody looks at — the kind of wrong that never raises an error.
    """

    PORTAL = Path(__file__).resolve().parent.parent / "app" / "portal.html"

    def test_portal_offers_exactly_the_canonical_categories(self):
        from app.store import CATEGORY_KEYS

        html = self.PORTAL.read_text(encoding="utf-8")
        block = re.search(r"var CATS = \[(.*?)\];", html, re.S)
        self.assertIsNotNone(block, "could not find the CATS array in portal.html")
        portal_keys = tuple(re.findall(r'"([^"]+)"', block.group(1)))
        self.assertEqual(portal_keys, CATEGORY_KEYS)

    def test_portal_labels_every_category_in_both_languages(self):
        from app.store import CATEGORY_KEYS

        html = self.PORTAL.read_text(encoding="utf-8")
        for lang in ("zh", "en"):
            start = html.index(f"{lang}: {{")
            cat = html.index("cat:{", start)
            labels = html[cat:html.index("act:{", cat)]
            for key in CATEGORY_KEYS:
                self.assertRegex(
                    labels, rf'"?{re.escape(key)}"?\s*:',
                    f"{lang} is missing a label for category {key!r}",
                )

    def test_portal_has_no_demo_backend(self):
        """A design pass arrived carrying the artifact's in-memory demo store,
        reachable via `if (!TOKEN) return demoApi(...)`. It never fired in
        production — the portal is only served at /t/<token> — but for a ledger,
        silently accepting writes into a fake is the worst failure available,
        so it must not come back on the next handoff.
        """
        html = self.PORTAL.read_text(encoding="utf-8")
        for marker in ("DEMO_EXP", "demoApi", "demoInit", "demoSummary"):
            self.assertNotIn(marker, html, f"demo scaffolding present: {marker}")

    def test_portal_always_talks_to_the_real_api(self):
        html = self.PORTAL.read_text(encoding="utf-8")
        self.assertIn('fetch("/api/"', html)
        self.assertNotRegex(html, r"if\s*\(\s*!TOKEN\s*\)\s*return")

    def test_borrow_is_the_category_with_arithmetic(self):
        """The portal must special-case exactly the key the store does."""
        from app.store import BORROW_CATEGORY

        html = self.PORTAL.read_text(encoding="utf-8")
        self.assertIn(f'var BORROW = "{BORROW_CATEGORY}"', html)


if __name__ == "__main__":
    unittest.main()
