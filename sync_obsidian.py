import asyncio
import fcntl
import os
import sys
from datetime import datetime
from pathlib import Path

# Pin cognee to the .env next to this script, so it runs the same from launchd,
# Obsidian or any other working directory. Must be set before importing cognee.
# Without that file (e.g. in Docker, where settings come as env vars) leave it unset.
SCRIPT_DIR = Path(__file__).resolve().parent
if (SCRIPT_DIR / ".env").is_file():
    os.environ.setdefault("COGNEE_ENV_FILE", str(SCRIPT_DIR / ".env"))

import cognee  # noqa: E402  (loads the .env into os.environ)
from cognee.tasks.ingestion.connectors import obsidian_source  # noqa: E402

VAULT_PATH = os.environ.get("OBSIDIAN_VAULT_PATH")
LOCK_PATH = os.environ.get("OBSIDIAN_SYNC_LOCK_PATH", str(SCRIPT_DIR / ".obsidian_sync.lock"))
DATASET = "obsidian"

if not VAULT_PATH:
    sys.exit("OBSIDIAN_VAULT_PATH is not set in .env")


async def sync():
    source = obsidian_source(vault_path=VAULT_PATH, resource_name="obs_tt")
    await cognee.remember(
        source,
        dataset_name=DATASET,
        primary_key="id",
        write_disposition="merge",  # required: the default "replace" breaks incremental sync
        max_rows_per_table=0,
        self_improvement=False,
    )
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] SYNC STATS:", source.cognee_sync_stats)


async def ask(question):
    results = await cognee.recall(query_text=question, datasets=[DATASET])
    for result in results:
        print("RECALL:", result.text)


if __name__ == "__main__":
    # python sync_obsidian.py            -> sync the vault
    # python sync_obsidian.py "question" -> search the vault
    if len(sys.argv) > 1:
        asyncio.run(ask(sys.argv[1]))
    else:
        # Only one sync per vault and dataset may run at a time (connector limitation).
        # The lock is released automatically when the process exits, even on a crash.
        lock_file = open(LOCK_PATH, "w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] SKIPPED: another sync is running")
            sys.exit(0)
        asyncio.run(sync())
