"""Single-owner MCP OAuth gateway, using the MCP SDK's OAuth protocol handlers.

Only the configured existing Cognee account may consent. This is not dataset
isolation: OAuth grants the same personal memory access as the legacy MCP token.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.routing import Route

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    RefreshToken, RegistrationError, TokenError, construct_redirect_uri,
)
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

SCOPE = "memory"
COOKIE = "__Host-cognee-oauth-flow"
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
FLOW_TTL = 600
CODE_TTL = 120


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path)
        if path != ":memory:":
            os.chmod(path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS records (
            kind TEXT, key TEXT, value TEXT NOT NULL, expires REAL NOT NULL,
            family TEXT, PRIMARY KEY(kind, key))""")
        self.db.commit()

    @staticmethod
    def key(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def put(self, kind: str, key: str, value: dict, expires: float, family: str = ""):
        self.db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?)",
                        (kind, self.key(key), json.dumps(value), expires, family))

    def get(self, kind: str, key: str):
        row = self.db.execute("SELECT value FROM records WHERE kind=? AND key=? AND expires>?",
                              (kind, self.key(key), time.time())).fetchone()
        return json.loads(row[0]) if row else None

    def consume(self, kind: str, key: str) -> bool:
        return self.db.execute("DELETE FROM records WHERE kind=? AND key=? AND expires>?",
                               (kind, self.key(key), time.time())).rowcount == 1

    def prune(self):
        self.db.execute("DELETE FROM records WHERE expires<=?", (time.time(),))


def safe_redirect(uri: str) -> bool:
    p = urlsplit(uri)
    return bool(p.hostname and not p.username and not p.password and not p.fragment
                and (p.scheme == "https" or (p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost", "::1"))))


class Provider:
    def __init__(self, store: Store, issuer: str):
        self.store, self.issuer = store, issuer.rstrip("/")
        self.resource = self.issuer + "/mcp"

    async def get_client(self, client_id: str):
        data = self.store.get("client", client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull):
        if not client_info.redirect_uris or len(client_info.redirect_uris) > 10 or any(
            not safe_redirect(str(uri)) for uri in client_info.redirect_uris
        ):
            raise RegistrationError("invalid_redirect_uri", "HTTPS or loopback redirect URIs required")
        if client_info.token_endpoint_auth_method not in ("none", "client_secret_post", "client_secret_basic"):
            raise RegistrationError("invalid_client_metadata", "Unsupported token authentication method")
        with self.store.db:
            self.store.prune()
            count = self.store.db.execute("SELECT count(*) FROM records WHERE kind='client'").fetchone()[0]
            if count >= 1000:
                raise RegistrationError("invalid_client_metadata", "Registration capacity reached")
            # Client registrations persist, including across container restarts.
            self.store.put("client", client_info.client_id, client_info.model_dump(mode="json"), 253402300799)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams):
        if params.resource not in (None, self.resource):
            raise AuthorizeError("invalid_request", "Unknown resource")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge):
            raise AuthorizeError("invalid_request", "S256 PKCE challenge required")
        if params.scopes is not None and set(params.scopes) != {SCOPE}:
            raise AuthorizeError("invalid_scope", "The memory scope is required")
        params.resource = self.resource
        params.scopes = [SCOPE]
        flow = secrets.token_urlsafe(32)
        with self.store.db:
            self.store.prune()
            self.store.put("flow", flow, {"client_id": client.client_id,
                "params": params.model_dump(mode="json")}, time.time() + FLOW_TTL)
        return self.issuer + "/oauth/login?" + urlencode({"flow": flow})

    async def load_authorization_code(self, client, authorization_code):
        data = self.store.get("code", authorization_code)
        if data and data["client_id"] == client.client_id:
            return AuthorizationCode(code=authorization_code, **data)

    def issue(self, client_id, subject, scopes, resource, family=None, refresh_expires=None):
        family = family or secrets.token_hex(16)
        now = int(time.time())
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        common = dict(client_id=client_id, subject=subject, scopes=scopes, resource=resource)
        self.store.put("access", access, dict(common, expires_at=now + ACCESS_TTL), now + ACCESS_TTL, family)
        refresh_expires = refresh_expires or now + REFRESH_TTL
        self.store.put("refresh", refresh, dict(common, expires_at=refresh_expires), refresh_expires, family)
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL,
                          refresh_token=refresh, scope=" ".join(scopes))

    async def exchange_authorization_code(self, client, authorization_code):
        with self.store.db:
            if not self.store.consume("code", authorization_code.code):
                raise TokenError("invalid_grant", "Code already used or expired")
            return self.issue(client.client_id, authorization_code.subject, authorization_code.scopes,
                              authorization_code.resource)

    async def load_refresh_token(self, client, refresh_token):
        data = self.store.get("refresh", refresh_token)
        if data and data["client_id"] == client.client_id:
            return RefreshToken(token=refresh_token, **data)
        used = self.store.get("refresh_used", refresh_token)
        if used and used["client_id"] == client.client_id:
            # Reuse of a rotated token signals a stolen credential. Revoke the
            # remaining family instead of letting the newer token stay active.
            with self.store.db:
                self.store.db.execute("DELETE FROM records WHERE family=?", (used["family"],))

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        with self.store.db:
            row = self.store.db.execute("SELECT family FROM records WHERE kind='refresh' AND key=?",
                                        (self.store.key(refresh_token.token),)).fetchone()
            if not row or not self.store.consume("refresh", refresh_token.token):
                raise TokenError("invalid_grant", "Refresh token already used or expired")
            # Rotation invalidates the old access token as well as the refresh token.
            self.store.db.execute("DELETE FROM records WHERE family=? AND kind IN ('access','refresh')", (row[0],))
            self.store.put("refresh_used", refresh_token.token,
                {"client_id": client.client_id, "family": row[0]}, refresh_token.expires_at, row[0])
            return self.issue(client.client_id, refresh_token.subject, scopes, refresh_token.resource,
                              family=row[0], refresh_expires=refresh_token.expires_at)

    async def load_access_token(self, token):
        data = self.store.get("access", token)
        return AccessToken(token=token, **data) if data else None

    async def revoke_token(self, token):
        kind = "refresh" if isinstance(token, RefreshToken) else "access"
        with self.store.db:
            row = self.store.db.execute("SELECT family FROM records WHERE kind=? AND key=?",
                                        (kind, self.store.key(token.token))).fetchone()
            if row:
                self.store.db.execute("DELETE FROM records WHERE family=?", (row[0],))


class Guard:
    """Bound public auth traffic and bodies; never log credentials or URL queries."""
    def __init__(self, app):
        self.app = app
        self.windows = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path in ("/register", "/authorize", "/oauth/login", "/token", "/revoke"):
            # The sidecar is reachable only through Caddy. Use global limits rather
            # than trusting spoofable forwarding headers for security decisions.
            bucket = "login" if path == "/oauth/login" and scope["method"] == "POST" else "auth"
            start, count = self.windows.get(bucket, (time.monotonic(), 0))
            period, limit = (300, 15) if bucket == "login" else (60, 120)
            if time.monotonic() - start >= period:
                start, count = time.monotonic(), 0
            self.windows[bucket] = start, count + 1
            if count >= limit:
                return await JSONResponse({"error": "temporarily_unavailable"}, 429,
                    headers={"Retry-After": str(period)})(scope, receive, send)
            total = 0
            original = receive
            async def limited_receive():
                nonlocal total
                message = await original()
                total += len(message.get("body", b""))
                if total > 16384:
                    raise ValueError("auth body too large")
                return message
            receive = limited_receive
        await self.app(scope, receive, send)


class ASGIEndpoint:
    def __init__(self, handler):
        self.handler = handler

    async def __call__(self, scope, receive, send):
        await self.handler(scope, receive, send)


def create_app(*, issuer=None, db_path=None, legacy_token=None, owner_email=None,
               api_url=None, upstream_url=None, transport=None):
    issuer = (issuer or os.environ["MCP_OAUTH_ISSUER"]).rstrip("/")
    if urlsplit(issuer).scheme != "https" or urlsplit(issuer).path:
        raise ValueError("MCP_OAUTH_ISSUER must be an HTTPS origin")
    owner = owner_email or os.environ["MCP_OAUTH_OWNER_EMAIL"]
    legacy = legacy_token if legacy_token is not None else os.environ["MCP_TOKEN"]
    if not owner or not legacy:
        raise ValueError("Owner account and existing MCP token required")
    api_url = (api_url or os.getenv("MCP_OAUTH_API_URL", "http://cognee-api:8000")).rstrip("/")
    upstream_url = (upstream_url or os.getenv("MCP_UPSTREAM_URL", "http://cognee-mcp:8000")).rstrip("/")
    store = Store(db_path or os.getenv("MCP_OAUTH_DB", "/state/oauth.sqlite3"))
    provider = Provider(store, issuer)
    http = httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False,
                            timeout=httpx.Timeout(60, read=None))

    @asynccontextmanager
    async def lifespan(app):
        yield
        await http.aclose()
        store.db.close()

    auth_routes = create_auth_routes(provider, AnyHttpUrl(issuer),
        client_registration_options=ClientRegistrationOptions(enabled=True,
            valid_scopes=[SCOPE], default_scopes=[SCOPE]),
        revocation_options=RevocationOptions(enabled=True))
    handlers = {r.path: r for r in auth_routes}

    async def metadata(request):
        return JSONResponse({"issuer": issuer, "authorization_endpoint": issuer + "/authorize",
            "token_endpoint": issuer + "/token", "registration_endpoint": issuer + "/register",
            "revocation_endpoint": issuer + "/revoke", "scopes_supported": [SCOPE],
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "code_challenge_methods_supported": ["S256"]})

    async def resource_metadata(request):
        return JSONResponse({"resource": provider.resource, "authorization_servers": [issuer],
            "scopes_supported": [SCOPE], "bearer_methods_supported": ["header"],
            "resource_name": "Cognee personal memory"})

    # Preserve the SDK ASGI endpoints, including its CORS and body limits.
    sdk_authorize = handlers["/authorize"].app
    sdk_token = handlers["/token"].app
    sdk_revoke = handlers["/revoke"].app

    async def authorize_asgi(scope, receive, send):
        req = Request(scope, receive)
        body = await req.body() if req.method != "GET" else b""
        params = req.query_params if req.method == "GET" else await req.form()
        if params.get("code_challenge_method") != "S256":
            return await JSONResponse({"error": "invalid_request", "error_description": "S256 PKCE required"}, 400)(scope, receive, send)
        # SDK parses the same body; replay it for POST requests.
        async def replay():
            return {"type": "http.request", "body": body, "more_body": False}
        await sdk_authorize(scope, replay if req.method != "GET" else receive, send)

    async def token_asgi(scope, receive, send):
        if scope["method"] == "OPTIONS":
            return await sdk_token(scope, receive, send)
        req = Request(scope, receive)
        body = await req.body()
        form = await req.form()
        resource = form.get("resource")
        # The SDK verifies PKCE/client/redirect/scopes; also bind the exchange to
        # this resource (the SDK's provider API does not receive this parameter).
        if resource not in (None, provider.resource) or (form.get("grant_type") == "authorization_code"
                and not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", str(form.get("code_verifier", "")))):
            return await JSONResponse({"error": "invalid_request"}, 400)(scope, receive, send)
        async def replay():
            return {"type": "http.request", "body": body, "more_body": False}
        await sdk_token(scope, replay, send)

    async def revoke_asgi(scope, receive, send):
        if scope["method"] == "OPTIONS":
            return await sdk_revoke(scope, receive, send)
        req = Request(scope, receive)
        form = dict(await req.form())
        # SDK 1.30's revocation model requires the optional client_secret field
        # to be present. Public clients legitimately omit it (RFC 7009).
        form.setdefault("client_secret", "")
        body = urlencode(form).encode()
        async def replay():
            return {"type": "http.request", "body": body, "more_body": False}
        await sdk_revoke(scope, replay, send)

    def page(flow, data, error=""):
        client_data = store.get("client", data["client_id"]) or {}
        params = data["params"]
        name = html.escape(client_data.get("client_name") or "MCP client")
        target = html.escape(urlsplit(params["redirect_uri"]).netloc)
        content = f"""<!doctype html><html lang="ko"><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Cognee 메모리 연결</title><style>
        body{{background:#111;color:#eee;font:16px system-ui;max-width:440px;margin:8vh auto;padding:24px}}
        input,button{{box-sizing:border-box;width:100%;padding:12px;margin:8px 0 16px;border-radius:8px}}
        button{{background:#7526ed;color:white;border:0;cursor:pointer}}small{{color:#bbb}}.error{{color:#ff9696}}
        </style><h1>Cognee 메모리 연결</h1>
        <p><strong>{name}</strong> 앱이 개인 메모리에 접근하려고 합니다.</p>
        <p>승인하면 기존 MCP와 같은 범위의 메모리를 검색·저장·삭제할 수 있습니다.</p>
        <small>앱 이름은 클라이언트가 제공한 이름입니다.<br>승인 후 돌아갈 주소: {target}</small>
        <p class="error">{html.escape(error)}</p><form method="post" action="/oauth/login">
        <input type="hidden" name="flow" value="{html.escape(flow)}">
        <label>Cognee UI 이메일<input name="email" type="email" autocomplete="username" required></label>
        <label>비밀번호<input name="password" type="password" autocomplete="current-password" required></label>
        <button name="action" value="allow" type="submit">로그인하고 연결 승인</button>
        <button name="action" value="deny" type="submit" formnovalidate>취소</button></form></html>"""
        return HTMLResponse(content, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            "X-Content-Type-Options": "nosniff"})

    async def login(request):
        form = await request.form() if request.method == "POST" else request.query_params
        flow = str(form.get("flow", ""))
        data = store.get("flow", flow)
        if not data:
            return HTMLResponse("로그인 요청이 만료되었습니다. 연결 앱에서 다시 시도하세요.", 400)
        if request.method == "GET":
            response = page(flow, data)
            response.set_cookie(COOKIE, flow, max_age=FLOW_TTL, secure=True, httponly=True,
                                samesite="lax", path="/")
            return response
        if request.headers.get("origin") != issuer or not hmac.compare_digest(
            request.cookies.get(COOKIE, "").encode(), flow.encode()
        ):
            return HTMLResponse("잘못된 로그인 요청입니다. 연결 앱에서 다시 시도하세요.", 403)
        params = AuthorizationParams.model_validate(data["params"])
        if form.get("action") == "deny":
            with store.db:
                store.consume("flow", flow)
            response = RedirectResponse(construct_redirect_uri(str(params.redirect_uri),
                error="access_denied", state=params.state, iss=issuer), 303)
            response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax")
            return response
        email, password = str(form.get("email", "")), str(form.get("password", ""))
        if form.get("action") != "allow" or email.casefold() != owner.casefold() or not password:
            return page(flow, data, "이메일 또는 비밀번호를 확인하세요.")
        try:
            # Do not keep the API session/cookie or forward it to the MCP backend.
            async with httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False,
                                         timeout=15) as auth_http:
                result = await auth_http.post(api_url + "/api/v1/auth/login",
                                               data={"username": email, "password": password})
                if result.status_code != 200:
                    return page(flow, data, "이메일 또는 비밀번호를 확인하세요.")
                who = await auth_http.get(api_url + "/api/v1/users/me")
                if who.status_code != 200 or who.json().get("email", "").casefold() != owner.casefold():
                    return page(flow, data, "허용된 계정으로 로그인하세요.")
                subject = str(who.json()["id"])
        except (httpx.HTTPError, ValueError, KeyError):
            return page(flow, data, "로그인 서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.")
        code = secrets.token_urlsafe(32)
        with store.db:
            if not store.consume("flow", flow):
                return HTMLResponse("이미 처리된 로그인 요청입니다.", 400)
            value = AuthorizationCode(code=code, client_id=data["client_id"],
                scopes=[SCOPE], expires_at=time.time() + CODE_TTL,
                code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                resource=provider.resource, subject=subject).model_dump(mode="json", exclude={"code"})
            store.put("code", code, value, value["expires_at"])
        response = RedirectResponse(construct_redirect_uri(str(params.redirect_uri), code=code,
                                     state=params.state, iss=issuer), 303)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax")
        return response

    async def proxy(request):
        auth = request.headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        valid = scheme.lower() == "bearer" and bool(token) and hmac.compare_digest(token.encode(), legacy.encode())
        if not valid and scheme.lower() == "bearer" and token:
            info = await provider.load_access_token(token)
            valid = bool(info and info.resource == provider.resource and SCOPE in info.scopes)
        if not valid:
            return JSONResponse({"error": "unauthorized"}, 401, headers={"WWW-Authenticate":
                f'Bearer resource_metadata="{issuer}/.well-known/oauth-protected-resource/mcp", scope="{SCOPE}"'})
        # Fixed upstream only. Strip credentials, cookies and forwarding headers;
        # preserve MCP session/protocol headers and stream SSE without buffering.
        headers = {k: v for k, v in request.headers.items() if k.lower() not in
            {"authorization", "cookie", "host", "connection", "transfer-encoding", "content-length", "accept-encoding"}
            and not k.lower().startswith(("x-forwarded-", "cf-")) and k.lower() != "forwarded"}
        headers["host"] = "localhost:8000"
        headers["accept-encoding"] = "identity"
        path = request.url.path
        query = ("?" + request.url.query) if request.url.query else ""
        upstream = http.build_request(request.method, upstream_url + path + query,
                                      headers=headers, content=request.stream())
        try:
            result = await http.send(upstream, stream=True)
        except httpx.HTTPError:
            return JSONResponse({"error": "upstream_unavailable"}, 502)
        response_headers = {k: v for k, v in result.headers.items() if k.lower() not in
                            {"connection", "transfer-encoding", "set-cookie", "content-encoding"}}
        async def chunks():
            try:
                async for chunk in result.aiter_bytes():
                    yield chunk
            finally:
                await result.aclose()
        return StreamingResponse(chunks(), status_code=result.status_code, headers=response_headers)

    routes = [r for r in auth_routes if r.path not in
              ("/.well-known/oauth-authorization-server", "/authorize", "/token", "/revoke")]
    routes += [Route("/.well-known/oauth-authorization-server", metadata),
        Route("/.well-known/oauth-protected-resource", resource_metadata),
        Route("/.well-known/oauth-protected-resource/mcp", resource_metadata),
        Route("/authorize", ASGIEndpoint(authorize_asgi), methods=["GET", "POST"]),
        Route("/token", ASGIEndpoint(token_asgi), methods=["POST", "OPTIONS"]),
        Route("/revoke", ASGIEndpoint(revoke_asgi), methods=["POST", "OPTIONS"]),
        Route("/oauth/login", login, methods=["GET", "POST"]),
        Route("/mcp", proxy, methods=["GET", "POST", "DELETE"]),
        Route("/health", proxy)]
    async def invalid_request(request, error):
        return JSONResponse({"error": "invalid_request"}, 400)

    app = Starlette(routes=routes, lifespan=lifespan, exception_handlers={ValueError: invalid_request})
    app.add_middleware(Guard)
    app.state.provider, app.state.http = provider, http
    return app
