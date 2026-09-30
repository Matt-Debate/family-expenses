"""OAuth resource-server boundary. Portal cookies never authorize MCP."""
from __future__ import annotations

import inspect
import json
import math
import logging
import os
import time
from pathlib import Path
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from urllib.parse import urlsplit

import anyio
import jwt
import mcp as mcp_sdk
from starlette.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.types import Tool

READ = "expenses:read"
WRITE = "expenses:write"
LINKS = "expenses:links"
SCOPES = frozenset((READ, WRITE, LINKS))
TOOL_SCOPES = {
    "expenses_help": READ, "expenses_list": READ, "expenses_history": READ,
    "classes_list": READ,
    "expenses_add": WRITE, "expenses_update": WRITE,
    "expenses_mark_paid": WRITE, "expenses_delete": WRITE,
    "expenses_refund": WRITE, "expenses_refund_delete": WRITE,
    "classes_add": WRITE, "classes_update": WRITE, "classes_delete": WRITE,
    "classes_log": WRITE, "classes_log_delete": WRITE,
    "expenses_list_links": LINKS, "expenses_mint_link": LINKS,
    "expenses_revoke_link": LINKS,
}


class SafeProtocolLogFilter(logging.Filter):
    """SDK errors/debug records can contain request bodies or validation values."""
    def filter(self, record):
        record.msg = "MCP protocol event (%s)"
        record.args = (record.levelname,)
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def protect_protocol_logs():
    # Root-level SDK logging bypasses named-logger filters. Sanitize records at
    # creation, before *any* handler (including capture/Cloud Logging) sees them.
    # Scope by the emitting SDK source path as well as the logger namespace;
    # unrelated application/root records keep their diagnostic detail.
    factory = logging.getLogRecordFactory()
    if getattr(factory, "_mcp_safe_records", False):
        return
    sdk_root = Path(mcp_sdk.__file__).resolve().parent
    scrubber = SafeProtocolLogFilter()

    def safe_records(*args, **kwargs):
        record = factory(*args, **kwargs)
        if (record.name == "mcp" or record.name.startswith("mcp.")
                or Path(record.pathname).resolve().is_relative_to(sdk_root)):
            scrubber.filter(record)
        return record
    safe_records._mcp_safe_records = True
    logging.setLogRecordFactory(safe_records)


def required_scopes(name: str) -> list[str]:
    required = TOOL_SCOPES.get(name, READ)
    return [READ] if required == READ else [READ, required]


class OAuthFastMCP(FastMCP):
    """Pinned SDK Tool allows extensions; mirror canonical OpenAI auth metadata."""
    async def list_tools(self):
        tools = await super().list_tools()
        return [Tool.model_validate({
            **tool.model_dump(by_alias=True, exclude_none=True),
            "securitySchemes": tool.meta["securitySchemes"],
        }) for tool in tools]


@dataclass(frozen=True)
class Principal:
    subject: str
    actor: str
    permissions: frozenset[str]


principal: ContextVar[Principal | None] = ContextVar("mcp_principal", default=None)


class AuthFailure(Exception):
    def __init__(self, status=401, error="invalid_token", required=READ):
        self.status, self.error, self.required = status, error, required


def authorize_tool(name: str) -> Principal:
    user = principal.get()
    if user is None:
        raise AuthFailure()
    required = TOOL_SCOPES.get(name)
    if READ not in user.permissions or required is None or required not in user.permissions:
        raise AuthFailure(403, "insufficient_scope", " ".join(required_scopes(name)))
    return user


def protected_tool(mcp, **options):
    """Guard at execution, even when invoked without the HTTP transport."""
    def decorate(fn):
        signature = inspect.signature(fn)
        @wraps(fn)
        def guarded(*args, **kwargs):
            user = authorize_tool(fn.__name__)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            for field in ("submitted_by", "changed_by", "logged_by"):
                if field in signature.parameters:
                    bound.arguments[field] = user.actor
            return fn(*bound.args, **bound.kwargs)
        return mcp.tool(meta={"securitySchemes": [
            {"type": "oauth2", "scopes": required_scopes(fn.__name__)}
        ]}, **options)(guarded)
    return decorate


@dataclass(frozen=True)
class AuthConfig:
    issuer: str
    resource: str
    members: dict

    @classmethod
    def from_env(cls):
        issuer = os.environ.get("MCP_AUTH_ISSUER", "").strip()
        resource = os.environ.get("MCP_RESOURCE_URL", "").strip()
        try:
            members = json.loads(os.environ.get("MCP_MEMBERS_JSON", ""))
            for value in (issuer, resource):
                url = urlsplit(value)
                if (url.scheme != "https" or not url.hostname or url.username
                        or url.password or url.query or url.fragment):
                    raise ValueError()
            if not issuer.endswith("/") or urlsplit(issuer).path != "/":
                raise ValueError()
            if urlsplit(resource).path != "/mcp":
                raise ValueError()
            if not isinstance(members, dict) or not members:
                raise ValueError()
            for subject, member in members.items():
                if not isinstance(subject, str) or not subject or not isinstance(member, dict):
                    raise ValueError()
                actor, permissions = member.get("actor"), member.get("permissions")
                if not isinstance(actor, str) or not actor.strip() or len(actor) > 100:
                    raise ValueError()
                if (not isinstance(permissions, list) or not permissions
                        or any(not isinstance(p, str) or p not in SCOPES for p in permissions)):
                    raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("MCP OAuth configuration missing or invalid") from None
        return cls(issuer, resource, members)

    @property
    def metadata_url(self):
        return self.resource.removesuffix("/mcp") + "/.well-known/oauth-protected-resource/mcp"

    def metadata(self):
        return {"resource": self.resource, "authorization_servers": [self.issuer],
                "scopes_supported": [READ], "bearer_methods_supported": ["header"]}


class TokenVerifier:
    def __init__(self, config: AuthConfig, *, clock=time.monotonic):
        self.config = config
        self.clock = clock
        self.keys = jwt.PyJWKClient(config.issuer + ".well-known/jwks.json",
                                    cache_jwk_set=False, timeout=5)
        # Do not use PyJWKClient.get_signing_key_from_jwt: it fetches on every
        # unknown kid. One process-wide verifier serializes/coalesces refreshes
        # and bounds misses/outages independently of attacker-chosen key IDs.
        self.refresh_cooldown = 60
        self.cache_lifetime = 300
        self._signing_keys = {}
        self._cache_until = 0
        self._last_attempt = float("-inf")
        self._refresh_lock = anyio.Lock()
        # Isolate network work from the default portal/database worker limiter.
        self.jwks_limiter = anyio.CapacityLimiter(1)

    async def signing_key(self, token: str):
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if (header.get("alg") != "RS256" or not isinstance(kid, str)
                or not kid or len(kid) > 256):
            raise AuthFailure()
        now = self.clock()
        if now < self._cache_until and kid in self._signing_keys:
            return self._signing_keys[kid]
        async with self._refresh_lock:
            now = self.clock()
            if now < self._cache_until and kid in self._signing_keys:
                return self._signing_keys[kid]
            if now - self._last_attempt >= self.refresh_cooldown:
                # Failed fetches also advance the cooldown; no per-kid memory.
                self._last_attempt = now
                data = await anyio.to_thread.run_sync(
                    self.keys.fetch_data, limiter=self.jwks_limiter)
                keyset = jwt.PyJWKSet.from_dict(data)
                signing_keys = {
                    key.key_id: key.key for key in keyset.keys
                    if key.key_id and key.key_type == "RSA"
                    and key.public_key_use in (None, "sig")
                    and key.algorithm_name == "RS256"
                }
                if not signing_keys:
                    raise AuthFailure()
                self._signing_keys = signing_keys
                self._cache_until = self.clock() + self.cache_lifetime
            if self.clock() >= self._cache_until or kid not in self._signing_keys:
                raise AuthFailure()
            return self._signing_keys[kid]

    async def verify(self, token: str) -> Principal:
        try:
            key = await self.signing_key(token)
            claims = jwt.decode(token, key, algorithms=["RS256"],
                                issuer=self.config.issuer, audience=self.config.resource,
                                options={"require": ["iss", "aud", "exp", "iat", "sub"]})
            for field in ("exp", "iat", "nbf"):
                if field in claims and (type(claims[field]) not in (int, float)
                                       or not math.isfinite(claims[field])):
                    raise AuthFailure()
            subject = claims["sub"]
            if not isinstance(subject, str):
                raise AuthFailure()
            member = self.config.members.get(subject)
            if member is None:
                raise AuthFailure(403, "access_denied")
            scope = claims.get("scope", "")
            permissions = claims.get("permissions", [])
            if (not isinstance(scope, str) or not isinstance(permissions, list)
                    or any(not isinstance(p, str) for p in permissions)):
                raise AuthFailure()
            # Scope = consent; permissions = Auth0 RBAC; local map = household policy.
            granted = frozenset(scope.split()) & frozenset(permissions) & frozenset(member["permissions"])
            return Principal(subject, member["actor"], granted)
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OverflowError):
            raise AuthFailure() from None


class McpBearerMiddleware:
    """Always protect MCP; incomplete local configuration returns 503.

    Production refuses to start without configuration. No static-secret or
    anonymous fallback exists. Body inspection provides HTTP scope challenges;
    execution guards enforce the same policy independently inside tools.
    """
    def __init__(self, app, protected_prefix="/mcp", *, config=None, verifier=None):
        self.app, self.prefix = app, protected_prefix
        try:
            self.config = config or AuthConfig.from_env()
        except ValueError:
            if os.environ.get("K_SERVICE"):
                raise
            self.config = None
        self.verifier = verifier or (TokenVerifier(self.config) if self.config else None)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            response = JSONResponse(self.config.metadata() if self.config else
                                    {"error": "oauth_not_configured"},
                                    status_code=200 if self.config else 503,
                                    headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)
        if path != self.prefix and not path.startswith(self.prefix + "/"):
            return await self.app(scope, receive, send)
        if self.config is None:
            return await JSONResponse({"error": "oauth_not_configured"}, status_code=503)(scope, receive, send)
        try:
            headers = [v for k, v in scope.get("headers", []) if k.lower() == b"authorization"]
            if not headers:
                raise AuthFailure(401, "", READ)
            if len(headers) != 1:
                raise AuthFailure()
            parts = headers[0].decode("ascii").split()
            if len(parts) != 2 or parts[0].lower() != "bearer" or len(parts[1]) > 16384:
                raise AuthFailure()
            user = await self.verifier.verify(parts[1])
            if READ not in user.permissions:
                raise AuthFailure(403, "insufficient_scope", READ)
            context = principal.set(user)
            try:
                if scope["method"] == "POST":
                    messages, body = [], b""
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        messages.append(message)
                        body += message.get("body", b"")
                        if len(body) > 1048576:
                            return await JSONResponse({"error": "request_too_large"}, status_code=413)(scope, receive, send)
                        if not message.get("more_body"):
                            break
                    try:
                        rpc = json.loads(body)
                    except (ValueError, UnicodeError):
                        return await JSONResponse({"error": "invalid_request"}, status_code=400)(scope, receive, send)
                    if not isinstance(rpc, dict):
                        return await JSONResponse({"error": "invalid_request"}, status_code=400)(scope, receive, send)
                    if rpc.get("method") == "tools/call":
                        params = rpc.get("params")
                        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                            return await JSONResponse({"error": "invalid_request"}, status_code=400)(scope, receive, send)
                        try:
                            authorize_tool(params["name"])
                        except AuthFailure as failure:
                            if failure.status != 403 or failure.error != "insufficient_scope":
                                raise
                            # Authenticated tool-level denial: MCP result metadata
                            # triggers ChatGPT consent/step-up without executing.
                            if (rpc.get("jsonrpc") != "2.0"
                                    or type(rpc.get("id")) not in (str, int)):
                                return await JSONResponse({"error": "invalid_request"}, status_code=400)(scope, receive, send)
                            challenge = self.challenge(failure)
                            result = {"jsonrpc": "2.0", "id": rpc["id"], "result": {
                                "content": [{"type": "text", "text": "Additional household permission is required."}],
                                "isError": True,
                                "_meta": {"mcp/www_authenticate": [challenge]},
                            }}
                            return await JSONResponse(result, headers={
                                "WWW-Authenticate": challenge, "Cache-Control": "no-store"
                            })(scope, receive, send)
                    async def replay():
                        if messages:
                            return messages.pop(0)
                        return await receive()
                    return await self.app(scope, replay, send)
                return await self.app(scope, receive, send)
            finally:
                principal.reset(context)
        except (AuthFailure, UnicodeError) as error:
            failure = error if isinstance(error, AuthFailure) else AuthFailure()
            challenge = self.challenge(failure)
            return await JSONResponse({"error": failure.error or "authentication_required"},
                                      status_code=failure.status,
                                      headers={"WWW-Authenticate": challenge, "Cache-Control": "no-store"})(scope, receive, send)

    def challenge(self, failure: AuthFailure) -> str:
        challenge = (f'Bearer resource_metadata="{self.config.metadata_url}", '
                     f'scope="{failure.required}"')
        if failure.error:
            challenge += f', error="{failure.error}", error_description="Household authorization required"'
        return challenge
