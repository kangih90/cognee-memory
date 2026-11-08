import asyncio
import os
from pathlib import Path

os.environ.setdefault("COGNEE_ENV_FILE", str(Path(__file__).resolve().parent / ".env"))

import cognee  # noqa: E402
from cognee.infrastructure.llm.LLMGateway import LLMGateway

async def main():
    out = await LLMGateway.acreate_structured_output(
        text_input="What is 2+3? Answer with just the number.",
        system_prompt="You are a concise assistant.",
        response_model=str,
    )
    print("LLM RESPONSE:", repr(out))

asyncio.run(main())
