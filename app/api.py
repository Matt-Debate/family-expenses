"""Framework-free API handlers.

Each handler takes ``(store, body)`` and returns ``(status, payload)`` —
testable without any web framework; ``app/web.py`` wires them to routes.

Every handler revalidates the access token (fail-closed, contract A5).
Error mapping: invalid token → 401, validation → 400, missing id → 404.
"""

from __future__ import annotations

from typing import Any

from .store import Store, ValidationError


def _authed(store: Store, body: dict) -> bool:
    return store.validate_token(body.get("token")) is not None


def _err(status: int, message: str) -> tuple[int, dict]:
    return status, {"ok": False, "error": message}


def _guard(handler):
    """Auth + error-translation wrapper shared by all handlers."""

    def wrapped(store: Store, body: Any) -> tuple[int, dict]:
        if not isinstance(body, dict):
            return _err(400, "invalid request body")
        if not _authed(store, body):
            return _err(401, "invalid or expired link")
        try:
            return handler(store, body)
        except ValidationError as exc:
            return _err(400, str(exc))
        except KeyError:
            return _err(404, "expense not found")

    return wrapped


@_guard
def api_list(store: Store, body: dict) -> tuple[int, dict]:
    expenses = store.list(
        status=body.get("status") or "all",
        since=body.get("since"),
        until=body.get("until"),
    )
    return 200, {
        "ok": True,
        "expenses": [e.to_dict() for e in expenses],
        "summary": store.summary(),
    }


@_guard
def api_submit(store: Store, body: dict) -> tuple[int, dict]:
    expense = store.create(
        date=body.get("date"),
        amount=body.get("amount"),
        currency=body.get("currency") or "CNY",
        category=body.get("category"),
        description=body.get("description"),
        submitted_by=body.get("submitted_by"),
    )
    return 200, {"ok": True, "expense": expense.to_dict()}


@_guard
def api_update(store: Store, body: dict) -> tuple[int, dict]:
    fields = body.get("fields")
    if not isinstance(fields, dict):
        raise ValidationError("fields object is required")
    expense = store.update(
        str(body.get("id")), fields=fields, changed_by=body.get("changed_by")
    )
    return 200, {"ok": True, "expense": expense.to_dict()}


@_guard
def api_mark_paid(store: Store, body: dict) -> tuple[int, dict]:
    expense = store.mark_paid(
        str(body.get("id")),
        paid=bool(body.get("paid")),
        paid_date=body.get("paid_date"),
        changed_by=body.get("changed_by"),
    )
    return 200, {"ok": True, "expense": expense.to_dict()}


@_guard
def api_delete(store: Store, body: dict) -> tuple[int, dict]:
    deleted = store.delete(str(body.get("id")), changed_by=body.get("changed_by"))
    if not deleted:
        raise KeyError(body.get("id"))
    return 200, {"ok": True}


@_guard
def api_history(store: Store, body: dict) -> tuple[int, dict]:
    entries = store.history(str(body.get("id")))
    return 200, {"ok": True, "history": [h.to_dict() for h in entries]}


HANDLERS = {
    "list": api_list,
    "submit": api_submit,
    "update": api_update,
    "mark-paid": api_mark_paid,
    "delete": api_delete,
    "history": api_history,
}
