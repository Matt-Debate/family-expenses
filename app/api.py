"""Framework-free API handlers.

Each handler takes ``(store, body)`` and returns ``(status, payload)`` —
testable without any web framework; ``app/web.py`` wires them to routes.

Every handler revalidates the access token (fail-closed, contract A5).
Error mapping: invalid token → 401, validation → 400, missing id → 404.
"""

from __future__ import annotations

from typing import Any

from .store import NotFoundError, Store, ValidationError, today_str


_LINK_LABEL = "_link_label"  # set by _guard from the validated token, never the client


def _authenticate(store: Store, body: dict) -> bool:
    """Validate the link and stamp its label onto the request.

    Portal writes carry no author — the form deliberately does not ask, since
    only one person types into it. But once more than one link is live, "an
    expense exists" and "who logged it" are different facts, and the audit trail
    needs the second. The label is taken from the validated token row and
    overwrites anything the client sent, so it cannot be spoofed.
    """
    link = store.validate_token(body.get("token"))
    body[_LINK_LABEL] = (link or {}).get("label")
    return link is not None


def _author(body: dict, explicit_key: str) -> Any:
    """The validated link's label, authoritatively.

    A client-supplied author is ignored outright. The portal form does not ask
    for a name, so anything arriving in the body is either noise or a spoof —
    a request bearing the "wife" link with submitted_by="Mallory" must not be
    able to write "Mallory" into the audit trail. MCP callers do not pass
    through here; they name the speaker via the store directly.
    """
    del explicit_key  # kept for call-site readability; deliberately unused
    return body.get(_LINK_LABEL)


def _err(status: int, message: str) -> tuple[int, dict]:
    return status, {"ok": False, "error": message}


def _guard(handler):
    """Auth + error-translation wrapper shared by all handlers."""

    def wrapped(store: Store, body: Any) -> tuple[int, dict]:
        if not isinstance(body, dict):
            return _err(400, "invalid request body")
        if not _authenticate(store, body):
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
    # read the clock ONCE: computing it separately for the summary and for the
    # response let a request straddling midnight bucket its rows against one
    # day and label them with the next
    today = today_str()
    return 200, {
        "ok": True,
        "expenses": [e.to_dict() for e in expenses],
        # summarize the rows we are actually returning: a filtered list under a
        # whole-ledger headline is a wrong number in the most visible place
        "summary": store.summarize(expenses, today=today),
        # the household's today (APP_TZ), so the page stops asking the phone.
        # The server already decides overdue-ness this way; when the two
        # disagreed — a travelling phone, a wrong clock — a row moved between
        # Due and Upcoming depending on who you asked.
        "today": today,
    }


@_guard
def api_submit(store: Store, body: dict) -> tuple[int, dict]:
    expense = store.create(
        date=body.get("date"),
        amount=body.get("amount"),
        currency=body.get("currency") or "CNY",
        category=body.get("category"),
        description=body.get("description"),
        submitted_by=_author(body, "submitted_by"),
    )
    return 200, {"ok": True, "expense": expense.to_dict()}


@_guard
def api_update(store: Store, body: dict) -> tuple[int, dict]:
    fields = body.get("fields")
    if not isinstance(fields, dict):
        raise ValidationError("fields object is required")
    expense = store.update(
        str(body.get("id")), fields=fields, changed_by=_author(body, "changed_by")
    )
    return 200, {"ok": True, "expense": expense.to_dict()}


@_guard
def api_mark_paid(store: Store, body: dict) -> tuple[int, dict]:
    expense = store.mark_paid(
        str(body.get("id")),
        paid=bool(body.get("paid")),
        paid_date=body.get("paid_date"),
        changed_by=_author(body, "changed_by"),
    )
    return 200, {"ok": True, "expense": expense.to_dict()}


@_guard
def api_delete(store: Store, body: dict) -> tuple[int, dict]:
    deleted = store.delete(str(body.get("id")), changed_by=_author(body, "changed_by"))
    if not deleted:
        raise NotFoundError(body.get("id"))
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
