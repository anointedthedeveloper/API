import asyncio, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from deepseek_manager import DeepSeekManager

async def main():
    m = DeepSeekManager()

    print("=== Non-stream ===")
    result = await m.chat_completion(
        messages=[{"role": "user", "content": "Say hi in one sentence"}],
        stream=False,
    )
    print("Content:", result["choices"][0]["message"]["content"])
    print("Usage:", result["usage"])

    print("\n=== Stream ===")
    gen = await m.chat_completion(
        messages=[{"role": "user", "content": "Count to 3"}],
        stream=True,
    )
    async for chunk in gen:
        print(chunk, end="", flush=True)

asyncio.run(main())
