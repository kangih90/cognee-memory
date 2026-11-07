"""Install the Obsidian -> cognee sync as a macOS LaunchAgent, configured from .env.

    python install_launchd.py              # write the plist and (re)load it
    python install_launchd.py --dry-run    # print the plist, change nothing
    python install_launchd.py --uninstall  # unload it and remove the plist

.env keys (all optional, defaults in brackets):
    OBSIDIAN_SYNC_INTERVAL  seconds between runs              [1800]
    OBSIDIAN_SYNC_PYTHON    interpreter that runs the sync    [the one running this script]
    OBSIDIAN_SYNC_LOG_DIR   where stdout/stderr logs go       [<script dir>/logs]

Run it again after changing any of these; it reloads the agent.
"""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

LABEL = "com.user.cognee-obsidian-sync"
SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
SYNC_SCRIPT = SCRIPT_DIR / "sync_obsidian.py"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
DOMAIN = f"gui/{os.getuid()}"


def build_plist(env: dict) -> dict:
    interval = int(env.get("OBSIDIAN_SYNC_INTERVAL") or 1800)
    if interval < 60:
        sys.exit("OBSIDIAN_SYNC_INTERVAL must be at least 60 seconds")
    python = Path(env.get("OBSIDIAN_SYNC_PYTHON") or sys.executable)
    log_dir = Path(env.get("OBSIDIAN_SYNC_LOG_DIR") or SCRIPT_DIR / "logs")

    if not python.is_file():
        sys.exit(f"Python interpreter not found: {python}")
    if not SYNC_SCRIPT.is_file():
        sys.exit(f"Sync script not found: {SYNC_SCRIPT}")

    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), str(SYNC_SCRIPT)],
        "WorkingDirectory": str(SCRIPT_DIR),
        "StartInterval": interval,
        "RunAtLoad": True,
        "StandardOutPath": str(log_dir / "obsidian-sync.log"),
        "StandardErrorPath": str(log_dir / "obsidian-sync.err.log"),
        "EnvironmentVariables": {
            "PATH": f"{python.parent}:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
            "PYTHONUNBUFFERED": "1",
        },
    }


def unload():
    # bootout fails when the agent is not loaded; that is fine here.
    subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{LABEL}"], capture_output=True)


def main():
    args = sys.argv[1:]

    if "--uninstall" in args:
        unload()
        PLIST_PATH.unlink(missing_ok=True)
        print(f"Uninstalled {LABEL}")
        return

    if not ENV_FILE.is_file():
        sys.exit(f".env not found: {ENV_FILE}")
    plist = build_plist(dotenv_values(ENV_FILE))

    if "--dry-run" in args:
        sys.stdout.write(plistlib.dumps(plist).decode())
        return

    Path(plist["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(plistlib.dumps(plist))

    unload()
    subprocess.run(["launchctl", "bootstrap", DOMAIN, str(PLIST_PATH)], check=True)
    print(f"Installed {PLIST_PATH}")
    print(f"Runs every {plist['StartInterval']}s; logs in {Path(plist['StandardOutPath']).parent}")


if __name__ == "__main__":
    main()
