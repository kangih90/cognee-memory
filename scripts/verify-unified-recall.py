"""Live API integration tests for patched Hermes read policy; never loads .env.

Loads the installed HTTP client and installed provider search methods. Formatting
and unrelated Hermes lifecycle hooks are isolated; retrieval uses real REST/DBs.
"""
import argparse
import ast
import concurrent.futures
import importlib.util
import json
import logging
from pathlib import Path
import threading
import time
import types
from typing import Any, Optional
import uuid

ROOT = Path.home() / "cognee"
REPORT = {}


def record(name, value):
    REPORT[name] = value
    (ROOT / "deploy/unified-recall-validation.json").write_text(json.dumps(REPORT, indent=2))
    print(name + "=" + json.dumps(value), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Read-only unified recall and source verification")
    parser.add_argument("--obsidian-query", required=True)
    parser.add_argument("--obsidian-needle", required=True)
    parser.add_argument("--hermes-query", required=True)
    parser.add_argument("--hermes-needle", required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("rest_validation_loader", ROOT / "scripts/verify-rest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = module.load_client()
    client.url = "http://127.0.0.1:18081"
    client.api_key = (ROOT / "deploy/secrets/hermes-api-key").read_text().strip()
    identity = json.loads((ROOT / "deploy/secrets/hermes-identity.json").read_text())
    requests = []
    original = client._request

    def traced(method, path, **kwargs):
        body = kwargs.get("json_body") or {}
        if path == "/api/v1/recall":
            # Retain routing fields only; never credentials or memory text.
            requests.append({key: body.get(key) for key in ["scope", "datasets", "dataset_ids", "only_context"]})
        return original(method, path, **kwargs)

    client._request = traced
    source = (Path.home() / ".hermes/plugins/cognee/cognee_integration_hermes/provider.py").read_text()
    tree = ast.parse(source)
    selected = {"_default_recall_datasets", "_recall", "_run_layered_prefetch", "_timeout", "_char_limit", "_is_not_found",
                "_normalize_recall_item", "_resolve_recall_provenance", "_handle_recall"}
    provider_class = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                          and any(isinstance(m, ast.FunctionDef) and m.name == "_recall" for m in node.body))
    methods = [node for node in provider_class.body if isinstance(node, ast.FunctionDef) and node.name in selected]
    assert len(methods) == len(selected)
    cls = ast.ClassDef(name="InstalledReadPolicy", bases=[], keywords=[], body=methods, decorator_list=[])
    minimal = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
    namespace = {
        "Any": Any, "Optional": Optional, "str_to_bool": lambda value, default=False: value if isinstance(value, bool) else default,
        "time": time, "json": json, "concurrent": concurrent, "logger": logging.getLogger("unified-recall-validation"),
        "_MEMORY_LANE": "cognee_memory", "_GUIDANCE_LANE": "cognee_guidance", "_SLOW_STREAK_THRESHOLD": 3,
        "_is_timeout": lambda error: isinstance(error, TimeoutError),
        "_coerce_result_dict": lambda item: item,
        "_result_text": lambda item: item.get("text") or "",
        "_recall_failure_advice": lambda error: "",
        "recall_text": types.SimpleNamespace(DEFAULT_PASSAGE_CHARS=2000, DEFAULT_CONTEXT_CHARS=12000,
            trim_recall_text=lambda text, *args, **kwargs: (text, "", {}), cap_total=lambda blocks, *args: blocks),
        "memory_credit": types.SimpleNamespace(credit_enabled=lambda: False),
    }
    exec(compile(minimal, "installed-provider-read-methods", "exec"), namespace)
    p = namespace["InstalledReadPolicy"]()
    p._config = {"recall_all_readable_datasets": True, "recall_timeout": 120, "recall_budget": 30}
    p._dataset = "hermes_default_memory"
    p._backend = client
    p._auto_route = True
    p._top_k = 5
    p._slow_streak = 0
    p._credit = types.SimpleNamespace(begin_turn=lambda: None)
    p._code_lane = lambda query: {}
    p._memory_lane_texts = lambda results: [str(item.get("text") or "") for item in results]
    p._record_success = lambda: None
    p._record_failure = lambda: None
    p._breaker_lock = threading.Lock()
    p._prefetch_lock = threading.Lock()
    p._prefetch_generation = 0
    p._prefetch_result = ""
    p._turns_seen = p._turns_with_hits = p._hits_total = 0
    p._hit_header = lambda hits: ""

    def recall(query, datasets=None):
        return p._recall(query, search_type="CHUNKS_LEXICAL", top_k=20, session_id="unused", datasets=datasets)

    def contains(result, text):
        return text in json.dumps(result, ensure_ascii=False)

    assert contains(recall(args.obsidian_query), args.obsidian_needle)
    assert requests[-1]["datasets"] is None and requests[-1]["dataset_ids"] is None
    record("default_recall_obsidian", True)
    marker = args.hermes_needle
    assert contains(recall(args.hermes_query), marker)
    record("default_recall_hermes_memory", True)

    assert contains(recall(args.obsidian_query, ["obsidian"]), args.obsidian_needle)
    assert requests[-1]["dataset_ids"] == [identity["obsidian_dataset_id"]]
    record("explicit_shared_obsidian_selector_sent", True)
    strict = not contains(recall(args.hermes_query, ["obsidian"]), marker)
    record("server_explicit_filter_is_strict", strict)
    if not strict:
        record("server_scope_limitation", "Shared graph/collection mode returns out-of-dataset chunks despite official dataset_ids filter")
    assert contains(recall(args.hermes_query, [identity["memory_dataset_id"]]), marker)
    record("explicit_uuid_filter", True)
    before = len(requests)
    try:
        recall(args.obsidian_query, ["nonexistent-" + uuid.uuid4().hex])
        raise AssertionError("Unknown dataset widened to default search")
    except ValueError:
        assert len(requests) == before
    record("unknown_filter_fails_closed", True)

    p._config["recall_all_readable_datasets"] = False
    assert contains(recall(args.hermes_query), marker)
    assert requests[-1]["dataset_ids"] == [identity["memory_dataset_id"]]
    record("opt_out_preserves_single_dataset_request", True)
    p._config["recall_all_readable_datasets"] = True
    before = len(requests)
    p._run_layered_prefetch(args.obsidian_query, "unified-read-validation-" + uuid.uuid4().hex, 0)
    lanes = requests[before:]
    graph = next(r for r in lanes if r["scope"] == ["graph"])
    guidance = next(r for r in lanes if r["scope"] == ["session_context"])
    assert graph["datasets"] is None and graph["dataset_ids"] is None and graph["only_context"] is True
    assert guidance["dataset_ids"] == [identity["memory_dataset_id"]]
    assert args.obsidian_needle in p._prefetch_result
    record("automatic_prefetch_reads_obsidian", True)
    record("session_guidance_stays_in_capture_dataset", True)
    record("unified_recall_checks", "passed")

    # Verify attribution against real relational document ownership. Credit text
    # uses the installed functions; only platform-logo rendering is isolated.
    credit_source = (Path.home() / ".hermes/plugins/cognee/cognee_integration_hermes/memory_credit.py").read_text()
    credit_ast = ast.parse(credit_source)
    credit_functions = [node for node in credit_ast.body if isinstance(node, ast.FunctionDef)
                        and node.name in {"_credit_rule", "tool_credit_instruction"}]
    credit_namespace = {"signs_with_logo": lambda platform: False}
    exec(compile(ast.fix_missing_locations(ast.Module(body=credit_functions, type_ignores=[])),
                 "installed-credit-functions", "exec"), credit_namespace)
    namespace["memory_credit"] = types.SimpleNamespace(
        credit_enabled=lambda: True, passage_origin=lambda text: "Cognee recall",
        tool_credit_instruction=credit_namespace["tool_credit_instruction"],
    )
    namespace["recall_text"].passage_session_id = lambda text: None
    p._platform = ""
    p._session_cognee_id = "provenance-read-validation"
    p._credit.label = lambda kind, origin, text: "[source: " + origin + "]"
    response = json.loads(p._handle_recall({"query": args.obsidian_needle, "search_type": "CHUNKS_LEXICAL", "top_k": 3}))
    assert not response.get("error")
    listening = [item for item in response["results"] if args.obsidian_needle in item["text"]]
    assert listening and any(item.get("dataset_name") == "obsidian" and item.get("provenance_status") == "verified"
                             for item in listening)
    # Later conversations may quote the note: verify the original's identity,
    # rather than asserting that no derived Hermes copy can ever exist.
    record("original_obsidian_document_source_verified", True)
    names = sorted({item.get("dataset_name") for item in response["results"] if item.get("dataset_name")})
    record("verified_returned_source_datasets", names)
    if len(names) > 1:
        assert "based on Cognee memory" in response["credit"]
        assert "based on dataset hermes_default_memory" not in response["credit"]
    record("mixed_sources_not_mislabelled_as_write_dataset", True)
    unknown = [{"text": "Aggregate result without document provenance", "dataset_name": "hermes_default_memory"}]
    p._resolve_recall_provenance(unknown)
    assert "dataset_name" not in unknown[0] and unknown[0]["provenance_status"] == "unverified"
    record("unverified_source_not_invented", True)
    record("provenance_checks", "passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        record("failure_type", type(error).__name__)
        tb = error.__traceback__
        while tb.tb_next:
            tb = tb.tb_next
        record("failure_line", tb.tb_lineno)
        raise SystemExit(1)
