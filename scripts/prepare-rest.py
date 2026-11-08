"""Run on the Mac mini deployment host: backup and prepare an additive official REST deployment.

Never opens any existing environment/credential file. Runtime settings are
obtained from Docker's running-container configuration and are never printed.
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path.home() / "cognee"
DOCKER = "/Applications/Docker.app/Contents/Resources/bin/docker"
os.umask(0o077)


def run(*args, **kwargs):
    return subprocess.run([DOCKER, *args], check=True, **kwargs)


def main():
    secret_dir = ROOT / "deploy" / "secrets"
    dest = secret_dir / "cognee-api.runtime"
    if dest.exists():
        raise RuntimeError("REST runtime already exists; refusing to replace it")
    info = json.loads(run("inspect", "deploy-cognee-mcp-1", capture_output=True).stdout)[0]
    assert "deploy_default" in info["NetworkSettings"]["Networks"]
    assert any(m.get("Name") == "deploy_cognee-storage" for m in info["Mounts"])
    old = dict(item.split("=", 1) for item in info["Config"]["Env"] if "=" in item)
    assert old.get("DB_PROVIDER") == "postgres"
    assert old.get("VECTOR_DB_PROVIDER") == "pgvector"
    assert old.get("GRAPH_DATABASE_PROVIDER") == "neo4j"
    assert old.get("CACHE_BACKEND") == "postgres"
    assert old.get("ENABLE_BACKEND_ACCESS_CONTROL", "").lower() == "false"

    backup = ROOT / "backups" / ("before-rest-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    backup.mkdir(parents=True, mode=0o700)
    for pattern in ("docker-compose*.yml", "Caddyfile*"):
        for path in (ROOT / "deploy").glob(pattern):
            shutil.copy2(path, backup / path.name)
    (backup / "metadata.json").write_text(json.dumps({"image": info["Image"], "core": "1.6.7", "mcp": "0.5.8"}))
    stopped = ["deploy-obsidian-sync-1", "deploy-cognee-mcp-1", "deploy-neo4j-1"]
    try:
        run("stop", "-t", "60", *stopped, stdout=subprocess.DEVNULL)
        with (backup / "postgres.dump").open("wb") as out:
            run("exec", "deploy-postgres-1", "pg_dump", "-U", "cognee", "-d", "cognee", "-Fc", stdout=out)
        with (backup / "storage.tgz").open("wb") as out:
            run("run", "--rm", "--user", "0", "-v", "deploy_cognee-storage:/backup/cognee:ro",
                "-v", "deploy_neo4j-data:/backup/neo4j:ro", "--entrypoint", "tar",
                "rclone/rclone:1.69", "-czf", "-", "-C", "/backup", ".", stdout=out)
    finally:
        run("start", "deploy-neo4j-1", "deploy-cognee-mcp-1", "deploy-obsidian-sync-1", stdout=subprocess.DEVNULL)

    secret_dir.mkdir(mode=0o700, exist_ok=True)
    secret_dir.chmod(0o700)
    prefixes = ("DB_", "VECTOR_DB_", "GRAPH_DATABASE_", "LLM_", "EMBEDDING_", "HUGGINGFACE_")
    exact = {"DATA_ROOT_DIRECTORY", "SYSTEM_ROOT_DIRECTORY", "HF_HOME", "HF_TOKEN", "CACHE_BACKEND", "CACHE_DB_URL", "CACHING"}
    env = {k: v for k, v in old.items() if k.startswith(prefixes) or k in exact}
    env.update({
        "ENABLE_BACKEND_ACCESS_CONTROL": "false", "REQUIRE_AUTHENTICATION": "true",
        "ENABLE_AUTO_MIGRATIONS": "false", "ENV": "prod", "TELEMETRY_DISABLED": "1",
        "ACCEPT_LOCAL_FILE_PATH": "false", "ALLOW_DATABASE_INGESTION": "false",
        "FASTAPI_USERS_JWT_SECRET": secrets.token_urlsafe(64),
        "FASTAPI_USERS_RESET_PASSWORD_TOKEN_SECRET": secrets.token_urlsafe(64),
        "FASTAPI_USERS_VERIFICATION_TOKEN_SECRET": secrets.token_urlsafe(64),
    })
    # Raw format makes dollar signs and quotes literal, rather than Compose substitutions.
    # JSON quoting is deliberately not used for shell commands or dotenv values.
    for value in env.values():
        if "\n" in value or "\r" in value:
            raise RuntimeError("Multiline runtime value requires manual secure setup")
    with dest.open("x") as out:
        out.write("".join(k + "=" + v + "\n" for k, v in sorted(env.items())))
    dest.chmod(0o600)
    (ROOT / "backups" / "latest-rest-backup").write_text(str(backup) + "\n")
    print("BACKUP=" + str(backup))
    print("REST_RUNTIME_PREPARED; no credentials displayed")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("PREPARE_FAILED=" + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
