"""Exercise the installed Hermes HTTP client without loading/changing Hermes config.

Run on the Mac mini deployment host. Credentials stay in memory, results contain only status/counts.
All writes/deletions target a newly generated validation dataset.
"""
import argparse
import ast
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import types
import urllib.error
import urllib.request
import uuid

sys.dont_write_bytecode = True
ROOT = Path.home() / "cognee"
SECRETS = ROOT / "deploy" / "secrets"
BASE = "http://127.0.0.1:18081"
DOCKER = "/Applications/Docker.app/Contents/Resources/bin/docker"
REPORT = {}
OBSIDIAN_QUERY = ""
OBSIDIAN_NEEDLE = ""


def record(name, result):
    REPORT[name] = result
    (ROOT / "deploy" / "rest-validation-report.json").write_text(json.dumps(REPORT, indent=2))
    print(name + "=" + json.dumps(result), flush=True)


def load_client():
    # Use the installed implementation; do not import the Hermes plugin entrypoint
    # or its credential/config resolver, which could load the active profile.
    package = types.ModuleType("rest_validation_hermes")
    package.__path__ = [str(Path.home() / ".hermes/plugins/cognee/cognee_integration_hermes")]
    sys.modules[package.__name__] = package
    config = types.ModuleType(package.__name__ + ".config")
    config.DEFAULT_USER_EMAIL = config.DEFAULT_USER_PASSWORD = ""
    config.LOCAL_COGNEE_REQUIREMENT = ""
    config.secret = lambda name: ""
    config.ollama_embedding_pins = lambda env: {}
    config.str_to_bool = lambda value, default=False: default
    sys.modules[config.__name__] = config
    return importlib.import_module(package.__name__ + ".http_backend").HttpBackend(agent_session_name="cognee-rest-validation")


def http(path, *, headers=None, body=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    h = dict(headers or {})
    if data is not None:
        h["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        return error.code, None


def recall(client, query, *, datasets, scope=None, session_id=None, kind="CHUNKS_LEXICAL"):
    return client.recall(query=query, session_id=session_id, datasets=datasets,
                         top_k=20, auto_route=True, query_type=kind,
                         scope=scope or ["graph"], timeout=180)


def has(result, text):
    return text.lower() in json.dumps(result, ensure_ascii=False).lower()


def wait_pipeline(client, dataset_id):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        status = client.dataset_pipeline_status(dataset_id=dataset_id, pipeline="cognify_pipeline", timeout=30)
        if status in {"DATASET_PROCESSING_COMPLETED", "COMPLETED", "PIPELINE_RUN_COMPLETED"} or ("COMPLETED" in status and "NOT" not in status):
            return status
        if "ERROR" in status or "FAIL" in status:
            raise RuntimeError("Validation pipeline reported failure")
        print("PIPELINE_WAIT=" + status, flush=True)
        time.sleep(5)
    raise TimeoutError("Validation pipeline did not complete")


def mcp_regression():
    info = json.loads(subprocess.check_output([DOCKER, "inspect", "deploy-caddy-1"]))[0]
    env = dict(x.split("=", 1) for x in info["Config"]["Env"] if "=" in x)
    headers = {"Authorization": "Bearer " + env["MCP_TOKEN"], "Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}

    def rpc(payload):
        req = urllib.request.Request("http://127.0.0.1:18080/mcp", data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=180) as response:
            if response.headers.get("Mcp-Session-Id"):
                headers["Mcp-Session-Id"] = response.headers["Mcp-Session-Id"]
            raw = response.read().decode()
        try:
            return json.loads(raw)
        except ValueError:
            return next(json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: "))

    result = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "rest-regression", "version": "1"}}})
    assert result["result"]["serverInfo"]["version"] == "0.5.8"
    result = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "recall", "arguments": {"query": OBSIDIAN_QUERY, "datasets": "obsidian", "search_type": "CHUNKS_LEXICAL", "top_k": 5}}})
    assert not result["result"].get("isError") and has(result, OBSIDIAN_NEEDLE)
    record("mcp_init_and_obsidian_recall", "passed")
    try:
        urllib.request.urlopen("http://127.0.0.1:18080/mcp", timeout=10)
        raise AssertionError("MCP accepted unauthenticated request")
    except urllib.error.HTTPError as error:
        assert error.code == 401
    record("mcp_unauthenticated", 401)


def main():
    global OBSIDIAN_QUERY, OBSIDIAN_NEEDLE
    parser = argparse.ArgumentParser(description="Validate REST in an isolated synthetic dataset")
    parser.add_argument("--obsidian-query", required=True)
    parser.add_argument("--obsidian-needle", required=True, help="Expected text in an existing Obsidian note")
    args = parser.parse_args()
    OBSIDIAN_QUERY, OBSIDIAN_NEEDLE = args.obsidian_query, args.obsidian_needle
    os.umask(0o077)
    key = (SECRETS / "hermes-api-key").read_text().strip()
    identity = json.loads((SECRETS / "hermes-identity.json").read_text())
    assert http("/health")[0] == 200
    for path, body in [("/api/v1/datasets", None), ("/api/v1/recall", {}),
                       ("/api/v1/remember/entry", {}), ("/api/v1/improve", {}), ("/api/v1/forget", {})]:
        assert http(path, body=body)[0] == 401
    assert http("/api/v1/datasets", headers={"X-Api-Key": "invalid-validation-key"})[0] == 401
    record("rest_missing_and_invalid_key", 401)
    status, schema = http("/openapi.json")
    assert status == 200
    required = {"/api/v1/recall": "post", "/api/v1/remember": "post", "/api/v1/remember/entry": "post",
                "/api/v1/improve": "post", "/api/v1/forget": "post", "/api/v1/datasets": "get",
                "/api/v1/datasets/status": "get", "/api/v1/datasets/{dataset_id}/data": "get",
                "/api/v1/datasets/{dataset_id}/data/{data_id}/raw": "get",
                "/api/v1/sessions": "get", "/api/v1/sessions/{session_id}": "get",
                "/api/v1/agents/register": "post", "/api/v1/agents/unregister": "post"}
    for path, method in required.items():
        assert method in schema["paths"][path], path
    (ROOT / "deploy" / "rest-openapi.json").write_text(json.dumps(schema))
    record("official_openapi_required_routes", len(required))
    client = load_client()
    client.connect(url=BASE, api_key=key, timeout=30)
    assert client.registered
    rows = client.list_datasets(timeout=30)
    assert any(r["id"] == identity["obsidian_dataset_id"] for r in rows)
    assert any(r["name"] == "hermes_default_memory" for r in rows)
    record("hermes_client_connect_register_datasets", "passed")
    obsidian_before = client.list_dataset_data(dataset_id=identity["obsidian_dataset_id"], timeout=30)
    assert len(obsidian_before) >= 1
    # Shared datasets are selected by dataset_ids in official REST, or all-readable
    # via the plugin's datasets=None. Names resolve only datasets owned by the key.
    result = recall(client, OBSIDIAN_QUERY, datasets=None)
    assert has(result, OBSIDIAN_NEEDLE)
    record("existing_obsidian_visible_to_hermes_key", True)
    denied, _ = http("/api/v1/forget", headers={"X-Api-Key": key}, body={
        "datasetId": identity["obsidian_dataset_id"], "dataId": str(uuid.uuid4())})
    assert denied == 403
    record("obsidian_delete_permission_denied", denied)

    suffix = uuid.uuid4().hex
    dataset = "hermes_rest_validation_" + suffix[:12]
    session = "hermes-rest-validation-" + suffix
    marker = "HERMESREST" + suffix
    session_marker = "HERMESSESSION" + suffix
    client.ensure_dataset(dataset=dataset, timeout=30)
    dataset_id = next(r["id"] for r in client.list_datasets(timeout=30) if r["name"] == dataset)
    (ROOT / "deploy" / "rest-validation-state.json").write_text(json.dumps({
        "dataset": dataset, "dataset_id": dataset_id, "session": session, "marker": marker, "session_marker": session_marker}))
    record("validation_dataset", dataset)
    permanent = f"Isolated verification {marker}: the permanent verification phrase is cobalt lantern."
    written = client.remember_permanent(text=permanent, dataset=dataset, session_ids=None, timeout=120)
    assert written.status in {"running", "completed"}
    record("permanent_write_initial_status", written.status)
    record("permanent_pipeline_final_status", wait_pipeline(client, dataset_id))
    result = recall(client, marker, datasets=[dataset])
    assert has(result, marker) and has(result, "cobalt lantern")
    record("permanent_recall_content_match", True)
    data = client.list_dataset_data(dataset_id=dataset_id, timeout=30)
    assert any(marker in client.read_raw_data(dataset_id=dataset_id, data_id=r["id"], timeout=30) for r in data)
    record("hermes_list_data_and_raw_read", "passed")

    session_text = f"User: Remember isolated verification {session_marker}.\nAssistant: Its session-only verification phrase is amber compass."
    written = client.remember_session(text=session_text, session_id=session, dataset=dataset, timeout=60)
    assert written.status == "session_stored"
    record("typed_session_write", written.status)
    result = recall(client, session_marker, datasets=[dataset], scope=["session"], session_id=session, kind=None)
    assert has(result, session_marker) and has(result, "amber compass")
    record("session_recall_content_match", True)
    assert not has(recall(client, session_marker, datasets=[dataset]), session_marker)
    record("session_absent_from_graph_before_improve", True)
    assert client.get_session(session_id=session, timeout=30) is not None
    client.list_sessions(range="all", limit=10, offset=0, timeout=30)
    record("hermes_session_inspection", "passed")

    improved = client.improve(dataset=dataset, session_ids=[session], background=False, timeout=600)
    assert improved["status"] == "completed", "Improve result did not complete"
    stages = {s["stage"]: s["status"] for s in improved["stages"]}
    assert stages["persist_session_qa"] in {"completed", "already_completed"}
    record("improve_final_status", improved["status"])
    record("improve_stages", stages)
    result = recall(client, session_marker, datasets=[dataset])
    assert has(result, session_marker) and has(result, "amber compass")
    record("session_promoted_to_permanent_graph", True)

    # Forget only the newly created test dataset's individual documents.
    data = client.list_dataset_data(dataset_id=dataset_id, timeout=30)
    deleted = 0
    for row in data:
        receipt = client.forget_document(dataset_id=dataset_id, data_id=row["id"], timeout=180)
        assert receipt.get("status") == "success" and receipt.get("data_remaining") is False
        deleted += 1
    record("test_documents_deleted", deleted)
    assert not client.list_dataset_data(dataset_id=dataset_id, timeout=30)
    assert not has(recall(client, marker, datasets=[dataset]), marker)
    assert not has(recall(client, session_marker, datasets=[dataset]), session_marker)
    record("post_delete_graph_and_document_absence", True)
    # Graph/document deletion and session deletion are separate lifecycle checks.
    receipt = client.forget(dataset=dataset, everything=False, memory_only=False, timeout=180)
    assert not any(r["id"] == dataset_id for r in client.list_datasets(timeout=30))
    session_status, _ = http("/api/v1/sessions/" + session, headers={"X-Api-Key": key}, method="DELETE")
    assert session_status in (200, 404)
    assert client.get_session(session_id=session, timeout=30) is None
    record("test_dataset_and_session_cleaned", True)
    obsidian_after = client.list_dataset_data(dataset_id=identity["obsidian_dataset_id"], timeout=30)
    assert {r["id"] for r in obsidian_before} == {r["id"] for r in obsidian_after}
    record("obsidian_document_ids_preserved", len(obsidian_after))
    client.close(timeout=30)
    record("hermes_agent_unregister", "passed")
    mcp_regression()
    result = subprocess.run([DOCKER, "exec", "deploy-obsidian-sync-1", "python", "/app/sync/sync_obsidian.py"], capture_output=True, text=True, timeout=600)
    assert result.returncode == 0
    matches = re.findall(r"SYNC STATS:\s*(\{[^\n]+\})", result.stdout)
    assert matches, "Sync did not produce final stats"
    stats = ast.literal_eval(matches[-1])
    assert stats.get("failed") == 0 and stats.get("failed_read", 0) == 0
    record("obsidian_sync", {k: v for k, v in stats.items() if isinstance(v, (int, bool))})
    record("all_checks", "passed")



if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not serialize exceptions: HTTP error bodies may contain memory text.
        record("failure_type", type(error).__name__)
        tb = error.__traceback__
        while tb.tb_next:
            tb = tb.tb_next
        record("failure_line", tb.tb_lineno)
        raise SystemExit(1)
