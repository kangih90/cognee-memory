"""Opt-in unified reads for the installed Hermes Cognee 1.4.1 plugin.

Only default cognee.json opts in. Storage/deletion code and server are unchanged.
Never opens .env or credentials. Run with --apply after reviewing --check.
"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError("Unexpected plugin source; refusing partial patch")
    return text.replace(old, new, 1)


def prepare(directory):
    edits = {}
    path = directory / "config.py"
    text = path.read_text()
    text = replace_once(text, '        "top_k": str_to_int(os.environ.get("COGNEE_TOP_K"), 5),', '''        # Local opt-in: omit the graph recall dataset filter, not the write target.
        "recall_all_readable_datasets": str_to_bool(
            os.environ.get("COGNEE_RECALL_ALL_READABLE_DATASETS"), False
        ),
        "top_k": str_to_int(os.environ.get("COGNEE_TOP_K"), 5),''')
    edits[path] = text

    path = directory / "provider.py"
    text = path.read_text()
    text = replace_once(text, '    def _recall(\n', '''    def _default_recall_datasets(self) -> Optional[list[str]]:
        """Read policy only: None lets Cognee search all readable datasets."""
        if str_to_bool(self._config.get("recall_all_readable_datasets"), False):
            return None
        return [self._dataset]

    def _recall(
''')
    start = text.index("    def _recall(\n")
    end = text.index("    def _remember_permanent(", start)
    section = text[start:end]
    section = replace_once(section, '        session_id: str,\n', '        session_id: str,\n        datasets: Optional[list[str]] = None,\n')
    section = replace_once(section, '            datasets=[self._dataset],', '            datasets=datasets if datasets is not None else self._default_recall_datasets(),')
    text = text[:start] + section + text[end:]
    text = replace_once(text,
        '                {"scope": ["graph"], "query_type": "HYBRID_COMPLETION", "session_id": None},',
        '''                {
                    "scope": ["graph"], "query_type": "HYBRID_COMPLETION", "session_id": None,
                    "datasets": self._default_recall_datasets(),
                },''')
    text = replace_once(text, '                    datasets=spec.get("datasets") or [self._dataset],',
        '                    datasets=spec["datasets"] if "datasets" in spec else [self._dataset],')
    start = text.index("    def _handle_recall(")
    end = text.index("    def _handle_remember(", start)
    section = text[start:end]
    section = replace_once(section, '        search_type = args.get("search_type")\n', '''        search_type = args.get("search_type")
        datasets = args.get("datasets")
        if "datasets" in args:
            if not isinstance(datasets, list) or not datasets or any(
                not isinstance(value, str) or not value.strip() for value in datasets
            ):
                return json.dumps({"error": "datasets must be a non-empty list of names or UUIDs"})
            datasets = [value.strip() for value in datasets]
''')
    section = replace_once(section, '                session_id=self._session_cognee_id,',
        '                session_id=self._session_cognee_id,\n                datasets=datasets,')
    text = text[:start] + section + text[end:]
    edits[path] = text

    path = directory / "http_backend.py"
    text = path.read_text()
    start = text.index("    def recall(\n")
    end = text.index("    def _remember(\n", start)
    section = text[start:end]
    section = replace_once(section, '''        if datasets:
            body["datasets"] = datasets''', '''        if datasets:
            # Official name selectors only resolve owned datasets. Translate an
            # explicit selection to authorized UUIDs so shared Obsidian works too.
            visible = self.list_datasets(timeout=timeout)
            selected_ids = []
            for selection in datasets:
                matches = [row for row in visible if str(row.get("id")) == str(selection)
                           or row.get("name") == selection]
                if len(matches) != 1:
                    raise ValueError("Dataset selection is unknown or ambiguous; use an accessible UUID")
                selected_ids.append(str(matches[0]["id"]))
            body["dataset_ids"] = list(dict.fromkeys(selected_ids))''')
    text = text[:start] + section + text[end:]
    edits[path] = text

    path = directory / "schemas.py"
    text = path.read_text()
    start = text.index("RECALL_SCHEMA = {")
    end = text.index("REMEMBER_SCHEMA = {", start)
    section = text[start:end]
    section = replace_once(section, '            "top_k": {', '''            "datasets": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": (
                    "Optional explicit dataset names or UUIDs to search. Omit to use "
                    "the configured read policy (all readable datasets with unified recall). "
                    "This filter never changes the storage or deletion dataset."
                ),
            },
            "top_k": {''')
    text = text[:start] + section + text[end:]
    edits[path] = text
    for path, text in edits.items():
        ast.parse(text, filename=str(path))
    return edits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    home = Path.home()
    directory = home / ".hermes/plugins/cognee/cognee_integration_hermes"
    if "def _default_recall_datasets(" in (directory / "provider.py").read_text():
        print("ALREADY_PATCHED; refusing to overwrite")
        return
    edits = prepare(directory)
    config = home / ".hermes/cognee.json"
    cfg = json.loads(config.read_text())
    assert cfg.get("transport") == "http"
    assert cfg.get("dataset") == "hermes_default_memory"
    cfg["recall_all_readable_datasets"] = True
    edits[config] = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    if not args.apply:
        print("CHECK_OK: four plugin source files; default profile read policy only")
        return
    os.umask(0o077)
    backup = home / "cognee/backups" / ("hermes-before-unified-recall-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    backup.mkdir(parents=True, mode=0o700)
    manifest = {}
    # Hash other profile configs without reading any .env file.
    profiles = home / ".hermes/profiles"
    others = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in profiles.glob("*/cognee.json")}
    for path, text in edits.items():
        shutil.copy2(path, backup / path.name)
        manifest[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        temporary = path.with_name(path.name + ".unified-recall.tmp")
        temporary.write_text(text)
        temporary.chmod(path.stat().st_mode & 0o777)
        temporary.replace(path)
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest for p, digest in others.items())
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("PATCH_APPLIED: default profile all-readable recall; explicit filters preserved")
    print("OTHER_PROFILE_CONFIGS_UNCHANGED=True")
    print("BACKUP=" + str(backup))


if __name__ == "__main__":
    main()
