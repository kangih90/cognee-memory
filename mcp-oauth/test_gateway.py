"""OAuth protocol and security regressions; all accounts and upstreams are fake."""
import base64
import hashlib
import json
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

import httpx

from gateway import COOKIE, Provider, Store, create_app

ORIGIN = "https://memory.example.com"
CALLBACK = "https://chat.example.com/callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.seen = []
        self.backend = httpx.MockTransport(self.mock_backend)
        self.app = create_app(issuer=ORIGIN, db_path=self.temp.name + "/oauth.sqlite3",
            legacy_token="legacy-secret", owner_email="owner@example.com", transport=self.backend,
            api_url="http://api", upstream_url="http://mcp")
        self.provider = self.app.state.provider
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(self.app), base_url=ORIGIN)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.app.state.http.aclose()
        self.provider.store.db.close()
        self.temp.cleanup()

    def mock_backend(self, request):
        self.seen.append(request)
        if request.url.path.endswith("/auth/login"):
            form = parse_qs(request.content.decode())
            if form.get("username") == ["owner@example.com"] and form.get("password") == ["correct"]:
                return httpx.Response(200, json={"access_token": "private-api-token"},
                                      headers={"set-cookie": "auth_token=private-api-token; Path=/; HttpOnly"})
            return httpx.Response(400, json={})
        if request.url.path.endswith("/users/me"):
            if "auth_token=private-api-token" not in request.headers.get("cookie", ""):
                return httpx.Response(401)
            return httpx.Response(200, json={"id": "owner-id", "email": "owner@example.com"})
        return httpx.Response(200, content=b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n',
            headers={"content-type": "text/event-stream", "mcp-session-id": "preserved-session"})

    async def register(self, **extra):
        response = await self.client.post("/register", json={"client_name": "Test MCP app",
            "redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], **extra})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def flow(self, client=None, **extra):
        client = client or await self.register()
        response = await self.client.get("/authorize", params={"client_id": client["client_id"],
            "redirect_uri": CALLBACK, "response_type": "code", "state": "original-state",
            "scope": "memory", "resource": ORIGIN + "/mcp", "code_challenge": CHALLENGE,
            "code_challenge_method": "S256", **extra})
        self.assertEqual(response.status_code, 302, response.text)
        page = await self.client.get(response.headers["location"])
        self.assertEqual(page.status_code, 200, page.text)
        self.assertIn("Test MCP app", page.text)
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        self.assertIn("Secure", page.headers["set-cookie"])
        flow = parse_qs(urlsplit(response.headers["location"]).query)["flow"][0]
        return client, flow

    async def consent(self, flow, **extra):
        return await self.client.post("/oauth/login", headers={"origin": ORIGIN}, data={
            "flow": flow, "email": "owner@example.com", "password": "correct", "action": "allow", **extra})

    async def code(self, client=None):
        client, flow = await self.flow(client)
        response = await self.consent(flow)
        self.assertEqual(response.status_code, 303, response.text)
        result = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(result["state"], ["original-state"])
        self.assertEqual(result["iss"], [ORIGIN])
        return client, result["code"][0]

    async def exchange(self, client, code, **extra):
        return await self.client.post("/token", data={"grant_type": "authorization_code",
            "client_id": client["client_id"], "code": code, "redirect_uri": CALLBACK,
            "code_verifier": VERIFIER, "resource": ORIGIN + "/mcp", **extra})

    async def tokens(self):
        client, code = await self.code()
        response = await self.exchange(client, code)
        self.assertEqual(response.status_code, 200, response.text)
        return client, response.json()

    async def test_discovery_challenge_and_anonymous_access(self):
        response = await self.client.post("/mcp", json={})
        self.assertEqual(response.status_code, 401)
        self.assertIn(ORIGIN + "/.well-known/oauth-protected-resource/mcp", response.headers["www-authenticate"])
        for path in ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"]:
            metadata = (await self.client.get(path)).json()
            self.assertEqual(metadata["resource"], ORIGIN + "/mcp")
        metadata = (await self.client.get("/.well-known/oauth-authorization-server")).json()
        self.assertEqual(metadata["code_challenge_methods_supported"], ["S256"])
        self.assertIn("none", metadata["token_endpoint_auth_methods_supported"])
        self.assertEqual((await self.client.get("/health")).status_code, 401)

    async def test_complete_flow_proxy_and_legacy(self):
        _, tokens = await self.tokens()
        for token in [tokens["access_token"], "legacy-secret"]:
            response = await self.client.post("/mcp", json={"jsonrpc": "2.0"}, headers={
                "authorization": "Bearer " + token, "mcp-session-id": "session-1",
                "cookie": "do-not-forward=secret", "x-forwarded-host": "attacker.example"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["mcp-session-id"], "preserved-session")
            self.assertIn("event: message", response.text)
            upstream = self.seen[-1]
            self.assertNotIn("authorization", upstream.headers)
            self.assertNotIn("cookie", upstream.headers)
            self.assertNotIn("x-forwarded-host", upstream.headers)
            self.assertEqual(upstream.headers["host"], "localhost:8000")
            self.assertEqual(upstream.headers["mcp-session-id"], "session-1")

    async def test_wrong_pkce_redirect_client_and_code_replay(self):
        client, code = await self.code()
        other = await self.register()
        for changes in [{"code_verifier": "x" * 64}, {"redirect_uri": "https://evil.example/callback"},
                        {"client_id": other["client_id"]}, {"resource": "https://evil.example/mcp"}]:
            self.assertNotEqual((await self.exchange(client, code, **changes)).status_code, 200)
        self.assertEqual((await self.exchange(client, code)).status_code, 200)
        self.assertNotEqual((await self.exchange(client, code)).status_code, 200)

    async def test_login_requires_origin_cookie_owner_password_and_consent(self):
        _, flow = await self.flow()
        for changes in [{"password": "wrong"}, {"email": "other@example.com"}, {"action": ""}]:
            response = await self.consent(flow, **changes)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("location", response.headers)
        response = await self.client.post("/oauth/login", headers={"origin": "https://evil.example"},
                                          data={"flow": flow, "action": "allow"})
        self.assertEqual(response.status_code, 403)
        self.client.cookies.clear()
        self.assertEqual((await self.consent(flow)).status_code, 403)
        self.assertFalse(any(r.url.path.endswith("/users/me") for r in self.seen))

    async def test_cancel_and_expired_flow(self):
        _, flow = await self.flow()
        response = await self.consent(flow, action="deny")
        self.assertEqual(response.status_code, 303)
        self.assertIn("error=access_denied", response.headers["location"])
        self.assertIn("state=original-state", response.headers["location"])
        self.assertEqual((await self.consent(flow)).status_code, 400)

    async def test_expiry_refresh_rotation_and_revocation(self):
        client, tokens = await self.tokens()
        # Tokens survive process replacement and raw access/refresh tokens are not stored.
        restarted = Store(self.temp.name + "/oauth.sqlite3")
        restored = Provider(restarted, ORIGIN)
        self.assertIsNotNone(await restored.load_access_token(tokens["access_token"]))
        persisted = str(restarted.db.execute("SELECT * FROM records").fetchall())
        self.assertNotIn(tokens["access_token"], persisted)
        self.assertNotIn(tokens["refresh_token"], persisted)
        restarted.db.close()
        response = await self.client.post("/token", data={"grant_type": "refresh_token",
            "client_id": client["client_id"], "refresh_token": tokens["refresh_token"],
            "resource": ORIGIN + "/mcp"})
        self.assertEqual(response.status_code, 200, response.text)
        rotated = response.json()
        self.assertIsNone(await self.provider.load_access_token(tokens["access_token"]))
        self.assertNotEqual(tokens["refresh_token"], rotated["refresh_token"])
        response = await self.client.post("/revoke", data={"client_id": client["client_id"],
            "token": rotated["refresh_token"], "token_type_hint": "refresh_token"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(await self.provider.load_access_token(rotated["access_token"]))

    async def test_refresh_replay_revokes_rotated_family(self):
        client, tokens = await self.tokens()
        form = {"grant_type": "refresh_token", "client_id": client["client_id"],
                "refresh_token": tokens["refresh_token"]}
        response = await self.client.post("/token", data=form)
        self.assertEqual(response.status_code, 200)
        rotated = response.json()
        response = await self.client.post("/token", data=form)
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(await self.provider.load_access_token(rotated["access_token"]))

    async def test_expired_code_access_and_scope_escalation(self):
        client, code = await self.code()
        with self.provider.store.db:
            self.provider.store.db.execute("UPDATE records SET expires=0 WHERE kind='code'")
        self.assertNotEqual((await self.exchange(client, code)).status_code, 200)
        client, tokens = await self.tokens()
        response = await self.client.post("/token", data={"grant_type": "refresh_token",
            "client_id": client["client_id"], "refresh_token": tokens["refresh_token"], "scope": "admin"})
        self.assertEqual(response.status_code, 400)
        with self.provider.store.db:
            self.provider.store.db.execute("UPDATE records SET expires=0 WHERE kind='access'")
        self.assertEqual((await self.client.post("/mcp", headers={
            "authorization": "Bearer " + tokens["access_token"]})).status_code, 401)

    async def test_registration_validation_and_pkce_required(self):
        for uri in ["javascript:alert(1)", "http://evil.example/callback", "https://evil.example/#fragment"]:
            response = await self.client.post("/register", json={"redirect_uris": [uri],
                "grant_types": ["authorization_code", "refresh_token"]})
            self.assertEqual(response.status_code, 400)
        client = await self.register()
        response = await self.client.get("/authorize", params={"client_id": client["client_id"],
            "redirect_uri": CALLBACK, "response_type": "code", "code_challenge": CHALLENGE})
        self.assertEqual(response.status_code, 400)
        response = await self.client.get("/authorize", params={"client_id": client["client_id"],
            "redirect_uri": "https://evil.example/callback", "response_type": "code",
            "code_challenge": CHALLENGE, "code_challenge_method": "S256"})
        self.assertEqual(response.status_code, 400)

    async def test_auth_body_limit(self):
        response = await self.client.post("/register", content=b"x" * 17000)
        self.assertEqual(response.status_code, 400)
        response = await self.client.post("/register", content=b"not-json",
                                          headers={"content-type": "application/json"})
        self.assertEqual(response.status_code, 400)

    async def test_confidential_clients_and_wrong_secret(self):
        for method in ["client_secret_post", "client_secret_basic"]:
            client = await self.register(token_endpoint_auth_method=method)
            client, code = await self.code(client)
            bad = await self.exchange(client, code, client_secret="incorrect")
            self.assertEqual(bad.status_code, 401)
            form = {"grant_type": "authorization_code", "client_id": client["client_id"],
                    "code": code, "redirect_uri": CALLBACK, "code_verifier": VERIFIER}
            kwargs = {}
            if method == "client_secret_post":
                form["client_secret"] = client["client_secret"]
            else:
                kwargs["auth"] = (client["client_id"], client["client_secret"])
            response = await self.client.post("/token", data=form, **kwargs)
            self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
