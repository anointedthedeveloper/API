import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from deepseek_manager import DeepSeekManager

async def main():
    m = DeepSeekManager()

    token = await m.get_valid_token()
    print(f"[1] Token: {repr(token[:30]) if token else 'NONE'}")

    if not token:
        print("No token — aborting")
        return

    pow_result = m._solve_pow("/api/v0/chat/completion")
    print(f"[2] POW result: {repr(pow_result[:80]) if pow_result else 'NONE'}")

    headers = m._get_chat_headers(token)
    print(f"[3] Has x-ds-pow-response: {'x-ds-pow-response' in headers}")

    # Send a real completion request
    result = await m.chat_completion(
        messages=[{"role": "user", "content": "Say hi"}],
        stream=False,
    )
    print(f"[4] Result: {result}")

asyncio.run(main())
