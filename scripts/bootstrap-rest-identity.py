"""Run inside the official API container as root once. Never prints secrets."""
import asyncio
import json
import os
from pathlib import Path

os.umask(0o077)


async def main():
    from sqlalchemy import select
    from cognee.infrastructure.databases.relational import get_relational_engine
    from cognee.modules.data.models import Dataset
    from cognee.modules.data.methods.create_authorized_dataset import create_authorized_dataset
    from cognee.modules.users.models import User
    from cognee.modules.users.methods import get_default_user, get_user
    from cognee.modules.users.methods.create_user import create_user
    from cognee.modules.users.api_key.create_api_key import create_api_key
    from cognee.modules.users.permissions.methods.give_permission_on_dataset import give_permission_on_dataset

    directory = Path("/run/cognee-secrets")
    keyfile = directory / "hermes-api-key"
    if keyfile.exists():
        raise RuntimeError("Identity credential already exists; refusing to rotate it")
    owner = await get_default_user()
    engine = get_relational_engine()
    email = "hermes-default@example.invalid"
    async with engine.get_async_session() as session:
        existing = (await session.execute(select(User).where(User.email == email))).scalars().first()
        obsidian = (await session.execute(select(Dataset).where(Dataset.name == "obsidian", Dataset.owner_id == owner.id))).scalars().one()
    if existing is not None:
        raise RuntimeError("Identity already exists; refusing to overwrite it")
    user = await create_user(email=email, password=None, is_superuser=False,
                             is_active=True, is_verified=True, parent_user_id=owner.id)
    user = await get_user(user.id)
    dataset = await create_authorized_dataset("hermes_default_memory", user)
    await give_permission_on_dataset(user, obsidian.id, "read")
    key = await create_api_key(user, name="hermes-default-rest")
    with keyfile.open("x") as out:
        out.write(key.api_key + "\n")
    keyfile.chmod(0o600)
    metadata = {"user_id": str(user.id), "api_key_id": str(key.id),
                "memory_dataset_id": str(dataset.id), "memory_dataset_name": dataset.name,
                "obsidian_dataset_id": str(obsidian.id), "parent_user_id": str(owner.id),
                "identity_email": email, "base_url": "http://127.0.0.1:18081"}
    (directory / "hermes-identity.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print("IDENTITY_CREATED; dedicated non-admin user; Obsidian read grant only; credential saved privately")


if __name__ == "__main__":
    asyncio.run(main())
