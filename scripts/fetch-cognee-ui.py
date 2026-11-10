"""Fetch the frontend matching our Cognee pin and adapt its remote API URLs."""
import io
from pathlib import Path
import shutil
import sys
import tarfile
import urllib.request

REF = "dcb8d7c6bf80a097005fb3aded6cdc3599225de8"
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "cognee-ui"

def fix_signout():
    route = DEST / "src/app/api/local-signout/route.ts"
    text = route.read_text()
    text = text.replace('new URL("/local-login", request.url)', 'new URL("/local-login", process.env.COGNEE_BACKEND_URL || request.url)')
    text = text.replace('response.cookies.set("fastapiusersauth", "", {', 'response.cookies.set("auth_token", "", {\n    httpOnly: true,\n    secure: true,\n    sameSite: "lax",')
    text = text.replace("Clear the fastapiusersauth cookie", "Clear the auth_token cookie")
    route.write_text(text)

if "--fix-signout" in sys.argv:
    fix_signout()
    print("UI signout cookie and HTTPS redirect corrected")
    raise SystemExit(0)

if DEST.exists():
    raise SystemExit(f"Refusing to overwrite {DEST}")
url = f"https://github.com/topoteretes/cognee/archive/{REF}.tar.gz"
with urllib.request.urlopen(url, timeout=120) as response:
    archive = response.read()
prefix = f"cognee-{REF}/cognee-frontend/"
with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
    for member in tar:
        if not member.name.startswith(prefix) or not member.isfile():
            continue
        relative = Path(member.name[len(prefix):])
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("Unsafe archive path")
        target = DEST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with tar.extractfile(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)

# This upstream revision still has direct build-time API URL readers. Bake the
# browser origin in, while server-side route handlers use the private API DNS.
dockerfile = DEST / "Dockerfile"
text = dockerfile.read_text()
assert text.count("RUN npm run build") == 1
dockerfile.write_text(text.replace("RUN npm run build", "ARG NEXT_PUBLIC_LOCAL_API_URL\nENV NEXT_PUBLIC_LOCAL_API_URL=$NEXT_PUBLIC_LOCAL_API_URL\nRUN npm run build"))
old = 'const localApiUrl = process.env.NEXT_PUBLIC_LOCAL_API_URL || "http://localhost:8000";'
for route in (DEST / "src/app/api").rglob("route.ts"):
    text = route.read_text()
    if old in text:
        route.write_text(text.replace(old, 'const localApiUrl = process.env.COGNEE_SERVER_BACKEND_URL || "http://cognee-api:8000";'))
login = DEST / "src/app/(auth)/local-login/partials/LocalSignInForm.tsx"
text = login.read_text().replace('const DEFAULT_EMAIL = "default_user@example.com";', 'const DEFAULT_EMAIL = "ui@example.com";').replace('const DEFAULT_PASSWORD = "default_password";', 'const DEFAULT_PASSWORD = "";').replace('Default credentials are pre-filled for local development', 'Sign in with your private UI account')
login.write_text(text)
fix_signout()
(DEST / ".source-ref").write_text(REF + "\n")
print(f"Prepared official frontend at {REF}")
