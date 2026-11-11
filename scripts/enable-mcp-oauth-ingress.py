"""Opt the existing cognee.wegle.com tunnel route into the OAuth endpoints.

Run on Mac mini. Validates before use and keeps a private timestamped backup.
Does not restart cloudflared; that is a separate deployment step.
"""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess

config = Path.home() / ".cloudflared/config.yml"
old = config.read_text()
before = "  - hostname: cognee.wegle.com\n    path: ^/mcp/?$\n"
after = ("  - hostname: cognee.wegle.com\n"
         "    path: '^/(mcp/?|[.]well-known/oauth-authorization-server|"
         "[.]well-known/oauth-protected-resource(/mcp)?|authorize|token|register|revoke|oauth/login)$'\n")
if after in old:
    print("OAuth ingress already configured")
    raise SystemExit(0)
if old.count(before) != 1:
    raise SystemExit("Expected existing MCP-only route not found; configuration left unchanged")
backup = config.with_name("config.yml.before-mcp-oauth-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
shutil.copy2(config, backup)
backup.chmod(0o600)
config.write_text(old.replace(before, after))
result = subprocess.run([str(Path.home() / ".local/bin/cloudflared"), "tunnel", "ingress", "validate"],
                        capture_output=True, text=True)
if result.returncode:
    config.write_text(old)
    raise SystemExit("Ingress validation failed; original configuration restored")
print("OAuth-only ingress extension validated; backup retained")
