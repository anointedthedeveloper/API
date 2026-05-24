import asyncio, sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from deepseek_manager import DeepSeekManager

async def main():
    m = DeepSeekManager()
    for i in range(2):
        t = time.perf_counter()
        result = await m.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            stream=False,
        )
        elapsed = (time.perf_counter() - t) * 1000
        print(f"Request {i+1}: {elapsed:.0f}ms  result={result}")

asyncio.run(main())
