import asyncio
from deepseek_manager import DeepSeekManager

async def test():
    m = DeepSeekManager()

    original = m.session.post
    def patched(url, **kwargs):
        if "chat/completion" in url and "create_pow" not in url:
            p = kwargs.get("json", {})
            print("PROMPT:", repr(p.get("prompt", ""))[:300])
            print("CONVERSATION:")
            for msg in p.get("conversation", []):
                print(f"  [{msg['role']}]: {repr(msg['content'])[:200]}")
        return original(url, **kwargs)
    m.session.post = patched

    result = await m.chat_completion(
        messages=[
            {"role": "system", "content": "You are ANAI. Current workspace: my-project."},
            {"role": "user", "content": "which folder am i working in"}
        ],
        stream=False,
    )
    print("ANSWER:", result["choices"][0]["message"]["content"])

asyncio.run(test())
