"""Bulk command secret source for Hermes; never run to display credentials.

Use --check for a status-only check. Normal output is for Hermes's captured pipe.
"""
import argparse
import os
from pathlib import Path
import re
import stat
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, default=Path.home() / "cognee/deploy/secrets/hermes-api-key")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = args.key_file.expanduser()
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise RuntimeError("Key file must be owned by this user and private (mode 600)")
    key = path.read_text().strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", key):
        raise RuntimeError("Expected a self-hosted Cognee API key")
    if args.check:
        print("KEY_FILE_OK; value not displayed")
    elif sys.stdout.isatty():
        raise RuntimeError("Refusing to display a credential in a terminal; use --check")
    else:
        sys.stdout.write("COGNEE_API_KEY=" + key + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("SECRET_HELPER_FAILED=" + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
