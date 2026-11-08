"""Fix unified-recall attribution without trusting shared-graph dataset labels."""
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError("Unexpected source; no changes applied")
    return text.replace(old, new, 1)


def main():
    path = Path.home() / ".hermes/plugins/cognee/cognee_integration_hermes/provider.py"
    original = path.read_text()
    if "def _resolve_recall_provenance(" in original:
        print("ALREADY_PATCHED")
        return
    assert "def _default_recall_datasets(" in original
    text = replace_once(original, '            envelope: dict[str, Any] = {"results": items, "count": len(items)}', '''            self._resolve_recall_provenance(items)
            envelope: dict[str, Any] = {"results": items, "count": len(items)}''')
    text = replace_once(text, '''                    item["tag"] = self._credit.label("recall", origin, item["text"])
                envelope["credit"] = memory_credit.tool_credit_instruction(
                    self._dataset, self._platform
                )''', '''                    if item.get("provenance_status") == "verified":
                        origin = "dataset " + item["dataset_name"]
                        if item.get("document_name"):
                            origin += " / " + item["document_name"]
                    item["tag"] = self._credit.label("recall", origin, item["text"])
                verified = {item["dataset_name"] for item in items
                            if item.get("provenance_status") == "verified"}
                all_verified = all(item.get("provenance_status") == "verified" for item in items)
                credit_source = next(iter(verified)) if all_verified and len(verified) == 1 else ""
                envelope["credit"] = memory_credit.tool_credit_instruction(
                    credit_source, self._platform
                ) + (
                    " Attribute facts only to each result's verified dataset_name and document_name. "
                    "Never treat the active write dataset as the source of a recalled fact. "
                    "Unverified or mixed results have no single verified dataset source."
                )''')
    text = replace_once(text, '''                                len(credit.found),
                                self._dataset,
                                self._platform,''', '''                                len(credit.found),
                                "",  # merged/context-only recall has no verified single source
                                self._platform,''')
    text = replace_once(text, '    def _normalize_recall_item(self, item: Any) -> dict[str, Any]:', '''    def _resolve_recall_provenance(self, items: list[dict[str, Any]]) -> None:
        """Verify chunk ownership against relational records, not graph labels.

        A bounded read-only lookup. Unknown/aggregate provenance stays unknown;
        a lookup failure must not invent a dataset or fail the memory search.
        """
        wanted = {str(item["data_id"]) for item in items if item.get("data_id")}
        for item in items:
            item.pop("dataset", None)
            item.pop("dataset_name", None)
            item.pop("dataset_id", None)
            item["provenance_status"] = "unverified"
        if not wanted:
            return
        found = {}
        deadline = time.monotonic() + 3.0
        try:
            datasets = self._backend.list_datasets(timeout=max(0.1, deadline - time.monotonic()))
            for dataset in datasets:
                offset = 0
                while wanted - found.keys() and time.monotonic() < deadline:
                    rows = self._backend.list_dataset_data(
                        dataset_id=str(dataset["id"]), limit=100, offset=offset,
                        timeout=max(0.1, deadline - time.monotonic()),
                    )
                    for row in rows:
                        data_id = str(row.get("id", ""))
                        if data_id in wanted:
                            found[data_id] = (str(dataset["id"]), str(dataset["name"]), str(row.get("name") or ""))
                    if len(rows) < 100:
                        break
                    offset += len(rows)
                if not wanted - found.keys():
                    break
        except Exception:
            logger.debug("Recall provenance lookup incomplete; unverified sources remain unnamed")
        for item in items:
            source = found.get(str(item.get("data_id", "")))
            if source:
                item["dataset_id"], item["dataset_name"], item["document_name"] = source
                item["provenance_status"] = "verified"

    def _normalize_recall_item(self, item: Any) -> dict[str, Any]:''')
    text = replace_once(text, '''        for key in ("score", "dataset", "dataset_name", "node_name"):
            if data.get(key) is not None:
                normalized[key] = data[key]
        return normalized''', '''        for key in ("score", "dataset", "dataset_name", "node_name"):
            if data.get(key) is not None:
                normalized[key] = data[key]
        metadata = data.get("metadata") or {}
        if isinstance(metadata, dict):
            for key in ("data_id", "document_name"):
                if metadata.get(key):
                    normalized[key] = str(metadata[key])
        return normalized''')
    ast.parse(text)
    os.umask(0o077)
    backup = Path.home() / "cognee/backups" / ("hermes-before-provenance-fix-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(path, backup / path.name)
    temp = path.with_name(path.name + ".provenance.tmp")
    temp.write_text(text)
    temp.chmod(path.stat().st_mode & 0o777)
    temp.replace(path)
    print("PROVENANCE_PATCH_APPLIED; no server or data changes")
    print("BACKUP=" + str(backup))


if __name__ == "__main__":
    main()
