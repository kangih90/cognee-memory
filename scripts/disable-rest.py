"""Explicit rollback: revoke only this integration key and remove only REST.

No volume deletion, data deletion, Hermes changes, or existing .env reads.
Existing Hermes memories are deliberately retained for recovery.
"""
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request


def main():
    root = Path.home() / "cognee"
    directory = root / "deploy/secrets"
    identity = json.loads((directory / "hermes-identity.json").read_text())
    key = (directory / "hermes-api-key").read_text().strip()
    request = urllib.request.Request(
        "http://127.0.0.1:18081/api/v1/auth/api-keys/" + identity["api_key_id"],
        headers={"X-Api-Key": key}, method="DELETE",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            assert response.status == 200
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise
        # A second rollback may find the key already revoked. Confirm access is
        # denied before taking the service down; never display the response.
        probe = urllib.request.Request("http://127.0.0.1:18081/api/v1/datasets", headers={"X-Api-Key": key})
        try:
            urllib.request.urlopen(probe, timeout=10)
            raise RuntimeError("Credential still accepted")
        except urllib.error.HTTPError as denied:
            assert denied.code == 401
    subprocess.run([
        "/Applications/Docker.app/Contents/Resources/cli-plugins/docker-compose",
        "--env-file", "/dev/null", "-p", "cognee-rest", "-f", "docker-compose.rest.yml", "down",
    ], cwd=root / "deploy", check=True)
    print("REST disabled and its key revoked; existing volumes and memories retained")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ROLLBACK_FAILED=" + type(error).__name__)
        raise SystemExit(1)
