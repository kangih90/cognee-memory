"""Run once as root inside the REST container. Does not print credentials."""
import asyncio
import json
import os
from pathlib import Path
import secrets

os.umask(0o077)

async def main():
    from sqlalchemy import select
    from cognee.infrastructure.databases.relational import get_relational_engine
    from cognee.modules.data.models import Dataset
    from cognee.modules.users.models import User
    from cognee.modules.users.methods.create_user import create_user
    from cognee.modules.users.permissions.methods.give_permission_on_dataset import give_permission_on_dataset

    directory = Path("/run/cognee-secrets")
    credential = directory / "ui-login.json"
    email = "ui@example.com"
    engine = get_relational_engine()
    async with engine.get_async_session() as session:
        existing = (await session.execute(select(User).where(User.email == email))).scalars().first()
        datasets = (await session.execute(select(Dataset))).scalars().all()
    if existing or credential.exists():
        raise RuntimeError("UI identity exists; refusing to rotate credentials")
    password = secrets.token_urlsafe(32)
    user = await create_user(email=email, password=password, is_superuser=False,
                             is_active=True, is_verified=True)
    # Personal instance: allow browsing, uploading and managing current datasets.
    # This does not grant instance administration or change existing identities.
    for dataset in datasets:
        for permission in ("read", "write", "delete"):
            await give_permission_on_dataset(user, dataset.id, permission)
    with credential.open("x") as output:
        json.dump({"email": email, "password": password, "user_id": str(user.id)}, output)
        output.write("\n")
    credential.chmod(0o600)
    print(f"UI identity created; granted access to {len(datasets)} existing datasets; credentials saved privately")

if __name__ == "__main__":
    asyncio.run(main())
