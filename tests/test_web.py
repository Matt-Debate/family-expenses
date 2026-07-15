"""Web-tier tests — full HTTP path via Starlette TestClient, sqlite-backed."""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
