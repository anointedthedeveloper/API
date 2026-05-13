import hashlib, json, base64, struct, requests

TOKEN = "nl0rw61/8lbKU6fRb4OmL374qy00nBBLWgzU7HhZKiiYsARE15lStyxleVaLMP54"

session = requests.Session()
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {TOKEN}",
    "x-app-version": "2.0.0",
    "x-client-locale": "en_US",
    "x-client-platform": "web",
    "x-client-version": "2.0.0",
    "Referer": "https://chat.deepseek.com/",
    "Origin": "https://chat.deepseek.com",
}

sr = session.post("https://chat.deepseek.com/api/v0/chat_session/create", headers=headers, json={"character_id": None})
session_id = sr.json()["data"]["biz_data"]["chat_session"]["id"]
print(f"Session: {session_id}")

pr = session.post("https://chat.deepseek.com/api/v0/chat/create_pow_challenge", headers=headers, json={"target_path": "/api/v0/chat/completion"})
biz = pr.json()["data"]["biz_data"]["challenge"]
salt, difficulty, algorithm, challenge, signature, expire_at = biz["salt"], biz["difficulty"], biz["algorithm"], biz["challenge"], biz["signature"], biz["expire_at"]
print(f"Difficulty: {difficulty}")

prefix = f"{salt}_{expire_at}_"
threshold = (2**32) // difficulty
answer = 0
while True:
    h = hashlib.sha3_256((challenge + prefix + str(answer)).encode()).digest()
    if struct.unpack("<I", h[:4])[0] < threshold:
        break
    answer += 1
print(f"Answer: {answer}")

pow_b64 = base64.b64encode(json.dumps({"algorithm": algorithm, "challenge": challenge, "salt": salt, "answer": answer, "signature": signature, "target_path": "/api/v0/chat/completion"}, separators=(",", ":")).encode()).decode()

cr = session.post("https://chat.deepseek.com/api/v0/chat/completion",
    headers={**headers, "x-ds-pow-response": pow_b64},
    json={"chat_session_id": session_id, "prompt": "Say hi in one word", "parent_message_id": None,
          "model": "deepseek_chat", "temperature": 0.7, "stream": False, "ref_file_ids": []})
print(f"Status: {cr.status_code}")
print(f"Response: {cr.text[:500]}")
