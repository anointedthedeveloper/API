import asyncio, sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
from deepseek_manager import DeepSeekManager

async def main():
    m = DeepSeekManager()
    for prompt in ["Hello", "create a html document like 10 lines", "what is 2+2"]:
        t = time.perf_counter()
        result = await m.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            stream=False,
        )
        elapsed = (time.perf_counter() - t) * 1000
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        err = result.get("error")
        print(f"[{elapsed:.0f}ms] prompt={prompt!r:.30s}  chars={len(content)}  err={err}")
        if content:
            print(f"  preview: {content[:100]!r}")

asyncio.run(main())
