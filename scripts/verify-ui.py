"""Verify the UI HTTPS origin with cookie auth and disposable synthetic data.

Run on the Mac mini. Never prints credentials, production document text or tokens.
"""
import http.cookiejar
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path.home() / "cognee"
BASE = "https://home-mini.geep-pompano.ts.net:8443"
report = {}
jar = http.cookiejar.CookieJar()
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def record(name, value):
    report[name] = value
    (ROOT / "deploy/ui-validation-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{name}={json.dumps(value)}", flush=True)

def request(path, body=None, method=None, headers=None):
    headers = dict(headers or {})
    if isinstance(body, dict):
        body = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
    try:
        with client.open(req, timeout=600) as response:
            raw = response.read()
            result = json.loads(raw) if "application/json" in response.headers.get("Content-Type", "") and raw else raw.decode()
            return response.status, result
    except urllib.error.HTTPError as error:
        return error.code, None

def main():
    assert request("/local-login")[0] == 200
    status, config = request("/api/runtime-config")
    assert status == 200 and BASE in json.dumps(config)
    record("ui_login_page_and_runtime_config", "passed")
    assert request("/api/v1/datasets")[0] == 401
    record("unauthenticated_datasets", 401)
    identity = json.loads((ROOT / "deploy/secrets/ui-login.json").read_text())
    body = urllib.parse.urlencode({"username": identity["email"], "password": identity["password"]}).encode()
    assert request("/api/v1/auth/login", body, headers={"Content-Type": "application/x-www-form-urlencoded"})[0] == 200
    cookie = next(c for c in jar if c.name == "auth_token")
    assert cookie.secure and cookie.has_nonstandard_attr("HttpOnly")
    assert request("/api/v1/users/me")[0] == 200
    status, rows = request("/api/v1/datasets")
    assert status == 200 and isinstance(rows, list)
    record("cookie_login_secure_httponly_and_datasets", len(rows))
    # The browser requests the trailing-slash route. Its canonical redirect
    # must stay on HTTPS after TLS terminates at Tailscale Serve.
    with client.open(BASE + "/api/v1/datasets/", timeout=60) as response:
        assert response.geturl() == BASE + "/api/v1/datasets"
        assert response.status == 200
        assert json.loads(response.read()) == rows
    record("datasets_slash_redirect_stays_https", "passed")
    for route in ("/", "/search", "/datasets"):
        assert request(route)[0] == 200
    record("app_routes", "passed")
    dataset = "ui_validation_" + uuid.uuid4().hex[:12]
    marker = "UIVERIFY" + uuid.uuid4().hex
    boundary = "----CogneeValidation" + uuid.uuid4().hex
    parts = []
    for key, value in (("datasetName", dataset), ("run_in_background", "false")):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n')
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="data"; filename="ui-validation.txt"\r\nContent-Type: text/plain\r\n\r\n{marker}: The synthetic UI verification phrase is violet telescope.\r\n')
    parts.append(f"--{boundary}--\r\n")
    status, result = request("/api/v1/remember", "".join(parts).encode(), headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    assert status == 200 and result.get("status") == "completed", "Synthetic upload did not complete"
    dataset_id = result["dataset_id"]
    record("file_upload_and_cognify", "completed")
    try:
        status, data = request(f"/api/v1/datasets/{dataset_id}/data")
        assert status == 200 and data
        status, result = request("/api/v1/recall", {"query": marker, "dataset_ids": [dataset_id], "search_type": "CHUNKS_LEXICAL", "top_k": 5})
        assert status == 200 and marker in json.dumps(result) and "violet telescope" in json.dumps(result)
        record("recall_content_match", True)
        # Exercise Next's server-side proxy with the browser cookie as well.
        status, _ = request("/api/visualize?dataset_id=" + dataset_id)
        assert status == 200
        record("server_side_visualize_proxy", "passed")
    finally:
        status, _ = request("/api/v1/forget", {"datasetId": dataset_id})
        assert status == 200
        record("synthetic_dataset_cleaned", True)
    assert request("/api/local-signout")[0] == 200
    assert request("/api/v1/datasets")[0] == 401
    record("logout", "passed")
    record("all_checks", "passed")

def regression():
    """Read-only checks of the existing API-key and MCP integrations."""
    report.update(json.loads((ROOT / "deploy/ui-validation-report.json").read_text()))
    key = (ROOT / "deploy/secrets/hermes-api-key").read_text().strip()
    req = urllib.request.Request("http://127.0.0.1:18081/api/v1/datasets", headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=30) as response:
        assert response.status == 200
    record("existing_hermes_key", "passed")
    docker = "/Applications/Docker.app/Contents/Resources/bin/docker"
    info = json.loads(subprocess.check_output([docker, "inspect", "deploy-caddy-1"]))[0]
    env = dict(item.split("=", 1) for item in info["Config"]["Env"] if "=" in item)
    headers = {"Authorization": "Bearer " + env["MCP_TOKEN"], "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    def rpc(payload):
        req = urllib.request.Request("https://home-mini.geep-pompano.ts.net/mcp", data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=60) as response:
            if response.headers.get("Mcp-Session-Id"):
                headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
            raw = response.read().decode()
        try:
            return json.loads(raw)
        except ValueError:
            return next(json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: "))
    result = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "ui-regression", "version": "1"}}})
    assert result["result"]["serverInfo"]["version"] == "0.5.8"
    result = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert any(tool["name"] == "recall" for tool in result["result"]["tools"])
    record("existing_mcp_https_initialize_and_tools", "passed")
    default_body = urllib.parse.urlencode({"username": "default_user@example.com", "password": "default_password"}).encode()
    assert request("/api/v1/auth/login", default_body, headers={"Content-Type": "application/x-www-form-urlencoded"})[0] == 400
    assert request("/api/visualize?dataset_id=" + str(uuid.uuid4()))[0] == 401
    record("default_demo_password_and_anonymous_visualize_denied", True)

def signout_check():
    report.update(json.loads((ROOT / "deploy/ui-validation-report.json").read_text()))
    identity = json.loads((ROOT / "deploy/secrets/ui-login.json").read_text())
    body = urllib.parse.urlencode({"username": identity["email"], "password": identity["password"]}).encode()
    assert request("/api/v1/auth/login", body, headers={"Content-Type": "application/x-www-form-urlencoded"})[0] == 200
    assert request("/api/v1/users/me")[0] == 200
    assert request("/api/local-signout")[0] == 200
    assert not any(c.name == "auth_token" for c in jar)
    assert request("/api/v1/datasets")[0] == 401
    record("ui_menu_signout_clears_auth_cookie", "passed")

if __name__ == "__main__":
    try:
        if "--regression-only" in sys.argv:
            regression()
        elif "--signout-only" in sys.argv:
            signout_check()
        else:
            main()
    except Exception as error:
        record("failure_type", type(error).__name__)
        tb = error.__traceback__
        while tb.tb_next:
            tb = tb.tb_next
        record("failure_line", tb.tb_lineno)
        raise SystemExit(1)
