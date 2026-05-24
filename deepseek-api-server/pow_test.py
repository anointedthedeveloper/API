"""
Test POW algorithm with a fresh token.
Get your token from: chat.deepseek.com → F12 → Application → Cookies → deepseek_token
Or use: curl -c cookies.txt https://chat.deepseek.com && grep deepseek_token cookies.txt
"""

import hashlib, json, base64, struct, requests, time
from datetime import datetime

# Get token from user or use stored one
TOKEN = input("Enter your DeepSeek token (from browser cookies): ").strip()
if not TOKEN:
    print("ERROR: Token required. Get it from chat.deepseek.com F12 → Application → Cookies → deepseek_token")
    exit(1)

session = requests.Session()
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}",
    "x-app-version": "2.0.0",
    "x-client-locale": "en_US",
    "x-client-platform": "web",
    "x-client-version": "2.0.0",
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://chat.deepseek.com/",
    "Origin": "https://chat.deepseek.com",
}

# Test token validity
print("\n✓ Testing token validity...")
status_resp = session.get("https://chat.deepseek.com/api/v0/users/current", headers=headers, timeout=10)
if status_resp.status_code != 200:
    print(f"✗ Token invalid! Status: {status_resp.status_code}")
    print(f"Response: {status_resp.text[:200]}")
    exit(1)
print("✓ Token is valid")

# Create session
print("\n✓ Creating chat session...")
sr = session.post("https://chat.deepseek.com/api/v0/chat_session/create", headers=headers, json={"character_id": None}, timeout=10)
if sr.status_code != 200:
    print(f"✗ Session creation failed: {sr.status_code}")
    print(f"Response: {sr.text[:200]}")
    exit(1)
session_id = sr.json()["data"]["biz_data"]["chat_session"]["id"]
print(f"✓ Session created: {session_id}")

# Fetch POW challenge
print("\n✓ Fetching POW challenge...")
pr = session.post("https://chat.deepseek.com/api/v0/chat/create_pow_challenge", headers=headers, json={"target_path": "/api/v0/chat/completion"}, timeout=10)
if pr.status_code != 200:
    print(f"✗ Challenge fetch failed: {pr.status_code}")
    print(f"Response: {pr.text[:200]}")
    exit(1)
biz = pr.json()["data"]["biz_data"]["challenge"]
salt, difficulty, algorithm, challenge, signature, expire_at = biz["salt"], biz["difficulty"], biz["algorithm"], biz["challenge"], biz["signature"], biz["expire_at"]
print(f"✓ POW challenge fetched (difficulty: {difficulty}, expires in {(biz['expire_after']/1000):.0f}s)")

# Solve POW
print("\n✓ Solving POW...")
start = time.time()
prefix = f"{salt}_{expire_at}_"
threshold = (2**32) // difficulty
answer = 0
while True:
    h = hashlib.sha3_256((challenge + prefix + str(answer)).encode()).digest()
    if struct.unpack("<I", h[:4])[0] < threshold:
        break
    answer += 1
elapsed = time.time() - start
print(f"✓ POW solved (answer={answer}, took {elapsed:.2f}s)")

# Build POW response
pow_response = {
    "algorithm": algorithm,
    "challenge": challenge,
    "salt": salt,
    "answer": answer,
    "signature": signature,
    "target_path": "/api/v0/chat/completion"
}
pow_b64 = base64.b64encode(json.dumps(pow_response, separators=(",", ":")).encode()).decode()

# Test chat completion with POW
print("\n✓ Testing chat/completion with POW...")
cr = session.post(
    "https://chat.deepseek.com/api/v0/chat/completion",
    headers={**headers, "x-ds-pow-response": pow_b64},
    json={
        "chat_session_id": session_id,
        "prompt": "Say 'POW works!' in one word",
        "parent_message_id": None,
        "model": "deepseek_chat",
        "temperature": 0.7,
        "stream": False,
        "ref_file_ids": []
    },
    timeout=60
)
print(f"Status: {cr.status_code}")
resp_data = cr.json()
print(f"Response: {json.dumps(resp_data, indent=2)[:500]}")

if resp_data.get("code") == 0:
    print("\n✅ SUCCESS! POW and chat completion working!")
    answer_text = resp_data.get("data", {}).get("answer", "No response")
    print(f"AI Response: {answer_text[:100]}")
else:
    print(f"\n❌ Failed with code {resp_data.get('code')}: {resp_data.get('msg')}")
