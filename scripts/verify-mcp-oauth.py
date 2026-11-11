"""Run on Mac mini: real public OAuth flow + read-only MCP checks; revoke at end.

Prints only check names. Never prints account credentials, codes, or tokens.
"""
import base64
import hashlib
import http.cookiejar
import json
from pathlib import Path
import secrets
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path.home() / "cognee"
BASE = "https://cognee.wegle.com"
RESOURCE = BASE + "/mcp"
CALLBACK = "http://127.0.0.1:18765/callback"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


jar = http.cookiejar.CookieJar()
client = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(jar))
client.addheaders = [("User-Agent", "Cognee-OAuth-Verification/1")]


def request(path, body=None, headers=None, form=False):
    headers = dict(headers or {})
    if body is not None:
        body = (urllib.parse.urlencode(body) if form else json.dumps(body)).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(BASE + path if path.startswith("/") else path, data=body, headers=headers)
    try:
        response = client.open(req, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = response.read()
        return response.code, response.headers, data


def check(name, condition):
    if not condition:
        raise RuntimeError("Check failed: " + name)
    print(name + ": passed", flush=True)


def decode_rpc(raw):
    text = raw.decode()
    if text.startswith("event:") or text.startswith("data:"):
        return next(json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: "))
    return json.loads(text)


def rpc_initialize_and_list(token):
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"}
    status, response_headers, raw = request("/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "oauth-readonly-verification", "version": "1"}}}, headers)
    assert status == 200 and decode_rpc(raw)["result"]["serverInfo"]["name"] == "Cognee"
    session = response_headers.get("Mcp-Session-Id")
    if session:
        headers["Mcp-Session-Id"] = session
    headers["MCP-Protocol-Version"] = "2025-03-26"
    status, _, _ = request("/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers)
    assert status in (200, 202, 204)
    status, _, raw = request("/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, headers)
    assert status == 200 and any(t["name"] == "recall" for t in decode_rpc(raw)["result"]["tools"])
    if session:
        req = urllib.request.Request(RESOURCE, method="DELETE", headers=headers)
        with client.open(req, timeout=10):
            pass


def main():
    status, headers, _ = request("/mcp", {})
    check("anonymous_denied_with_oauth_challenge", status == 401 and "resource_metadata=" in headers.get("WWW-Authenticate", ""))
    status, _, raw = request("/.well-known/oauth-protected-resource/mcp")
    check("resource_discovery", status == 200 and json.loads(raw)["resource"] == RESOURCE)
    status, _, raw = request("/.well-known/oauth-authorization-server")
    check("authorization_discovery", status == 200 and json.loads(raw)["code_challenge_methods_supported"] == ["S256"])
    status, _, raw = request("/register", {"client_name": "Read-only OAuth verification",
        "redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "scope": "memory"})
    check("dynamic_registration", status == 201)
    client_id = json.loads(raw)["client_id"]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    query = urllib.parse.urlencode(dict(client_id=client_id, redirect_uri=CALLBACK, response_type="code",
        code_challenge=challenge, code_challenge_method="S256", state=state, scope="memory", resource=RESOURCE))
    status, headers, _ = request("/authorize?" + query)
    check("authorize_to_login", status == 302 and headers["location"].startswith(BASE + "/oauth/login?"))
    login_url = headers["location"]
    flow = urllib.parse.parse_qs(urllib.parse.urlsplit(login_url).query)["flow"][0]
    status, headers, raw = request(login_url)
    check("secure_consent_page", status == 200 and "Secure" in headers.get("Set-Cookie", "") and b"Read-only OAuth verification" in raw)
    identity = json.loads((ROOT / "deploy/secrets/ui-login.json").read_text())
    status, headers, _ = request("/oauth/login", {"flow": flow, "action": "allow",
        "email": identity["email"], "password": identity["password"]}, {"Origin": BASE}, form=True)
    check("existing_ui_account_login", status == 303 and headers["location"].startswith(CALLBACK))
    callback = urllib.parse.parse_qs(urllib.parse.urlsplit(headers["location"]).query)
    check("callback_state_and_issuer", callback.get("state") == [state] and callback.get("iss") == [BASE])
    exchange = dict(grant_type="authorization_code", client_id=client_id, code=callback["code"][0],
                    redirect_uri=CALLBACK, code_verifier=verifier, resource=RESOURCE)
    status, _, raw = request("/token", exchange, form=True)
    check("pkce_token_exchange", status == 200)
    tokens = json.loads(raw)
    try:
        status, _, _ = request("/token", exchange, form=True)
        check("code_replay_denied", status == 400)
        rpc_initialize_and_list(tokens["access_token"])
        check("oauth_mcp_initialize_and_tools", True)
        status, _, raw = request("/token", dict(grant_type="refresh_token", client_id=client_id,
            refresh_token=tokens["refresh_token"], resource=RESOURCE), form=True)
        check("refresh_rotation", status == 200 and json.loads(raw)["refresh_token"] != tokens["refresh_token"])
        tokens = json.loads(raw)
        rpc_initialize_and_list(tokens["access_token"])
        check("refreshed_mcp_initialize_and_tools", True)
    finally:
        status, _, _ = request("/revoke", dict(client_id=client_id, token=tokens["refresh_token"],
            token_type_hint="refresh_token"), form=True)
        check("revoke_verification_grant", status == 200)
    status, _, _ = request("/mcp", {}, {"Authorization": "Bearer " + tokens["access_token"]})
    check("revoked_access_denied", status == 401)
    env = {}
    for line in (ROOT / "deploy/.env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip("\"'")
    rpc_initialize_and_list(env["MCP_TOKEN"])
    check("legacy_bearer_mcp_initialize_and_tools", True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Avoid tracebacks containing response bodies, credentials or tokens.
        print("FAILED: " + (str(error) if isinstance(error, RuntimeError) else type(error).__name__))
        raise SystemExit(1)
