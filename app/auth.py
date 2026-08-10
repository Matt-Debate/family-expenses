"""Portal OAuth — Auth0 Universal Login, layered on top of the portal token.

Pattern copied from work-dashboards (same Auth0 tenant, JP region), with one
deliberate divergence: that app is a Vite SPA using ``@auth0/auth0-spa-js`` with
bearer tokens in localStorage. This portal is a single self-contained HTML file
with no build step, and a CDN script tag is an unreliable dependency from
mainland China — so the code exchange happens server-side and the session lives
in a signed, httpOnly cookie.

WHAT THIS DOES NOT CHANGE (FEATURE_CONTRACT §5.1):

  * ``/t/<token>`` stays the portal path and the token still identifies the
    household. OAuth adds *who you are* on top of *which ledger*; it does not
    replace the link.
  * ``/mcp`` is untouched. ``MCP_SECRET`` stays unset. A connected MCP client
    never sees a login.

OFF BY DEFAULT. ``is_enabled()`` is false unless every required env var is set,
mirroring work-dashboards' ``VITE_AUTH_REQUIRED`` gate. With them unset the
portal behaves exactly as it did before this module existed, which is what keeps
the compatibility contract intact until the owner deliberately opts in.

Env:
  AUTH0_DOMAIN           e.g. work-os.jp.auth0.com   (no scheme)
  AUTH0_CLIENT_ID        Regular Web Application client id
  AUTH0_CLIENT_SECRET    from Secret Manager, never a literal env var
  SESSION_SECRET         signs the session cookie; rotating it logs everyone out
  PORTAL_ALLOWED_EMAILS  comma-separated allowlist. Auth0 authenticates anyone
                         who can create an account, so without this the login
                         page is an open door. Empty = deny everyone, chosen so
                         a half-finished config fails closed rather than open.
"""

from __future__ import annotations

import os
from typing import Any, Optional

_REQUIRED = ("AUTH0_DOMAIN", "AUTH0_CLIENT_ID", "AUTH0_CLIENT_SECRET", "SESSION_SECRET")

SESSION_USER_KEY = "portal_user"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def is_enabled() -> bool:
    """True only when every required var is present. Partial config = off."""
    return all(_env(name) for name in _REQUIRED)


def session_secret() -> str:
    return _env("SESSION_SECRET")


def allowed_emails() -> frozenset[str]:
    raw = _env("PORTAL_ALLOWED_EMAILS")
    return frozenset(
        part.strip().lower() for part in raw.split(",") if part.strip()
    )


def is_allowed(email: Optional[str]) -> bool:
    """Allowlist check. Fails closed: no list, or no email, means no entry."""
    if not email:
        return False
    return email.strip().lower() in allowed_emails()


def build_oauth():
    """Return a configured Authlib client, or None when OAuth is disabled.

    Uses Auth0's OIDC discovery document so the authorize/token/JWKS endpoints
    and ID-token validation all come from the tenant rather than being
    hand-assembled here.
    """
    if not is_enabled():
        return None
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        name="auth0",
        client_id=_env("AUTH0_CLIENT_ID"),
        client_secret=_env("AUTH0_CLIENT_SECRET"),
        server_metadata_url=f"https://{_env('AUTH0_DOMAIN')}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email"},
    )
    return oauth


def current_user(request) -> Optional[dict[str, Any]]:
    """The signed-in user from the session, or None.

    Always None when OAuth is disabled, so callers can treat "no user" and
    "auth off" as separate questions via is_enabled().
    """
    if not is_enabled():
        return None
    try:
        user = request.session.get(SESSION_USER_KEY)
    except Exception:  # SessionMiddleware absent (auth off) — not an error
        return None
    return user if isinstance(user, dict) else None


def logout_url(return_to: str) -> str:
    """Auth0 logout, which clears the tenant session too.

    Clearing only our cookie would leave Auth0 still signed in, so the next
    'login' would silently reuse the old identity and look like the logout
    never happened.
    """
    from urllib.parse import urlencode

    params = urlencode({"client_id": _env("AUTH0_CLIENT_ID"), "returnTo": return_to})
    return f"https://{_env('AUTH0_DOMAIN')}/v2/logout?{params}"
