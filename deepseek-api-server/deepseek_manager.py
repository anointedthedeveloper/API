import json
import sqlite3
import time
import asyncio
import logging
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from pow_native import DeepSeekPOW

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


@dataclass
class AuthStatus:
    is_authenticated: bool
    email: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    error: Optional[str] = None


class DeepSeekManager:

    def __init__(self, db_path: str = "deepseek_credentials.db"):
        self.db_path = db_path
        self.api_url = "https://chat.deepseek.com/api/v0"
        self._session_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://chat.deepseek.com/",
            "Origin": "https://chat.deepseek.com",
            "Content-Type": "application/json",
            "x-app-version": "20241129.1",
        }
        self.session = self._make_session()
        self._pow_cache: Optional[Dict] = None
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._init_database()

    def _make_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(self._session_headers)
        return s

    def _init_database(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS credentials (
                    id INTEGER PRIMARY KEY,
                    email TEXT,
                    access_token TEXT,
                    refresh_token TEXT,
                    token_expires_at TEXT,
                    last_login TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def _extract_token_string(self, token_input) -> Optional[str]:
        if not token_input:
            return None
        if isinstance(token_input, bytes):
            try:
                token_input = token_input.decode("utf-8")
            except Exception:
                return None
        if isinstance(token_input, str):
            token_input = token_input.strip()
            try:
                parsed = json.loads(token_input)
                if isinstance(parsed, dict):
                    token = parsed.get("value") or parsed.get("token")
                    if token:
                        return str(token).strip()
            except json.JSONDecodeError:
                pass
            return token_input
        if isinstance(token_input, dict):
            token = token_input.get("value") or token_input.get("token")
            if token:
                return str(token).strip()
        return None

    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except Exception:
            return None

    def _format_datetime(self, dt: datetime) -> str:
        return dt.isoformat()

    def _get_current_time(self) -> datetime:
        return datetime.utcnow()

    def _store_credentials(self, email: str, access_token: str, expires_in: int) -> None:
        expires_at = self._get_current_time() + timedelta(seconds=expires_in)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO credentials
                (id, email, access_token, refresh_token, token_expires_at, last_login)
                VALUES (1, ?, ?, ?, ?, ?)
            """, (email, access_token, access_token,
                  self._format_datetime(expires_at),
                  self._format_datetime(self._get_current_time())))
            conn.commit()

    def _get_stored_credentials(self) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM credentials WHERE id = 1")
            row = cursor.fetchone()
            if row:
                return dict(row)
        return None

    def _clear_credentials(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM credentials WHERE id = 1")
            conn.commit()

    def _solve_pow(self, target_path: str, token: Optional[str] = None) -> Optional[str]:
        """Fetch and solve DeepSeek Proof-of-Work challenge (uses its own session — thread-safe)."""
        try:
            sess = self._make_session()  # fresh session per thread to avoid pool conflicts
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            resp = sess.post(
                f"{self.api_url}/chat/create_pow_challenge",
                json={"target_path": target_path},
                headers=headers,
                timeout=15,
            )
            data = resp.json()
            if data.get("code") != 0:
                logger.warning("POW challenge fetch failed: %s", data.get("msg"))
                return None

            biz = data["data"]["biz_data"]["challenge"]
            config = {
                "algorithm": biz["algorithm"],
                "challenge": biz["challenge"],
                "salt": biz["salt"],
                "difficulty": biz["difficulty"],
                "expire_at": biz["expire_at"],
                "signature": biz["signature"],
                "target_path": target_path,
            }
            logger.info("Solving POW difficulty=%d", config["difficulty"])
            return DeepSeekPOW().solve_challenge(config)
        except Exception as e:
            logger.error("POW solve error: %s", e)
            return None

    def _get_chat_headers(self, token: str) -> Dict[str, str]:
        """Build full headers including POW for chat/completion."""
        pow_response = self._solve_pow("/api/v0/chat/completion", token=token)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-app-version": "2.0.0",
            "x-client-locale": "en_US",
            "x-client-platform": "web",
            "x-client-timezone-offset": "3600",
            "x-client-version": "2.0.0",
            "Referer": "https://chat.deepseek.com/",
            "Origin": "https://chat.deepseek.com",
        }
        if pow_response:
            headers["x-ds-pow-response"] = pow_response
        return headers

    async def login_with_manual_token(self, token_input) -> AuthStatus:
        token = self._extract_token_string(token_input)
        if not token:
            return AuthStatus(is_authenticated=False, error="Could not extract token.")
        self._store_credentials("deepseek_user", token, expires_in=7 * 24 * 3600)
        logger.info("Token stored (length: %d)", len(token))
        return AuthStatus(
            is_authenticated=True,
            email="deepseek_user",
            token_expires_at=self._get_current_time() + timedelta(days=7),
            last_login=self._get_current_time(),
        )

    async def _get_user_info(self, token: str) -> Optional[Dict]:
        """Fetch user profile to validate token."""
        try:
            response = self.session.get(
                f"{self.api_url}/users/current",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            logger.debug("User info status: %d body: %s", response.status_code, response.text[:200])
            if response.status_code != 200:
                return None
            data = response.json()
            if data.get("code") != 0:
                return None
            return data.get("data") or {}
        except Exception as e:
            logger.error("User info error: %s", e)
            return None

    async def login_with_email(self, email: str, password: str) -> AuthStatus:
        logger.info("Attempting email login for: %s", email)
        try:
            self.session.get("https://chat.deepseek.com/", timeout=10)
            response = self.session.post(
                f"{self.api_url}/users/login",
                json={"email": email, "password": password},
                timeout=30,
            )
            if response.status_code != 200:
                return AuthStatus(is_authenticated=False, error=f"Login failed: HTTP {response.status_code}")

            data = response.json()
            if data.get("code") != 0:
                return AuthStatus(is_authenticated=False, error=data.get("msg", "Login failed"))

            response_data = data.get("data", {})
            token = (response_data.get("token")
                     or response_data.get("access_token")
                     or response_data.get("value"))

            if not token:
                return AuthStatus(is_authenticated=False, error="No token in response")

            token = self._extract_token_string(token)
            email_actual = response_data.get("email", email)
            self._store_credentials(email_actual, token, expires_in=86400)

            return AuthStatus(
                is_authenticated=True,
                email=email_actual,
                token_expires_at=self._get_current_time() + timedelta(seconds=86400),
                last_login=self._get_current_time(),
            )
        except Exception as e:
            logger.exception("Email login error")
            return AuthStatus(is_authenticated=False, error=str(e))

    async def login_with_google(self, id_token: str) -> AuthStatus:
        try:
            response = self.session.post(
                f"{self.api_url}/users/oauth/google",
                json={"id_token": id_token},
                timeout=30,
            )
            if response.status_code != 200:
                return AuthStatus(is_authenticated=False, error=f"Google login failed: HTTP {response.status_code}")

            data = response.json()
            if data.get("code") != 0:
                return AuthStatus(is_authenticated=False, error=data.get("msg", "Google login failed"))

            token = data.get("data", {}).get("token")
            if not token:
                return AuthStatus(is_authenticated=False, error="No token in response")

            email = data.get("data", {}).get("email", f"google_user_{int(time.time())}")
            self._store_credentials(email, token, expires_in=86400)

            return AuthStatus(
                is_authenticated=True,
                email=email,
                token_expires_at=self._get_current_time() + timedelta(seconds=86400),
                last_login=self._get_current_time(),
            )
        except Exception as e:
            logger.exception("Google login error")
            return AuthStatus(is_authenticated=False, error=str(e))

    async def get_valid_token(self) -> Optional[str]:
        creds = self._get_stored_credentials()
        if not creds:
            return None
        token = creds.get("access_token")
        if not token:
            return None
        expires_at = self._parse_datetime(creds.get("token_expires_at"))
        if expires_at and self._get_current_time() >= expires_at - timedelta(minutes=5):
            logger.warning("Stored token has expired")
            return None
        return token

    async def _create_chat_session(self, token: str) -> Optional[str]:
        for attempt in range(2):  # retry once on connection error
            try:
                response = self.session.post(
                    f"{self.api_url}/chat_session/create",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"character_id": None},
                    timeout=30,
                )
                logger.debug("Session create status: %d body: %s", response.status_code, response.text[:300])
                data = response.json()
                if data.get("code") != 0:
                    logger.warning("Session creation failed: %s", data.get("msg"))
                    return None
                session_id = (data.get("data", {}).get("biz_data", {}).get("chat_session", {}).get("id")
                              or data.get("data", {}).get("biz_data", {}).get("id")
                              or data.get("data", {}).get("id")
                              or data.get("id"))
                logger.info("Created chat session: %s", session_id)
                return session_id
            except requests.exceptions.ConnectionError as e:
                logger.warning("Session create connection error (attempt %d): %s", attempt + 1, e)
                if attempt == 0:
                    self.session = self._make_session()  # reset the shared session and retry
                    continue
                return None
            except Exception as e:
                logger.exception("Session creation error")
                return None

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        temperature: float = 0.7,
        model: str = "deepseek-chat",
    ):
        token = await self.get_valid_token()
        if not token:
            error_msg = {"error": "Not authenticated. Please paste your token first."}
            return self._error_stream(error_msg) if stream else error_msg

        # Run session creation and POW solve in parallel
        loop = asyncio.get_event_loop()
        session_task = asyncio.ensure_future(self._create_chat_session(token))
        pow_task = loop.run_in_executor(self._executor, self._solve_pow, "/api/v0/chat/completion", token)
        session_id, pow_response = await asyncio.gather(session_task, pow_task)

        if not session_id:
            error_msg = {"error": "Failed to create chat session. Your token may be invalid or expired."}
            return self._error_stream(error_msg) if stream else error_msg

        try:
            # Merge system messages into the first user message
            merged = []
            system_text = ""
            for m in messages:
                if m.get("role") == "system":
                    system_text += m.get("content", "") + "\n\n"
                else:
                    merged.append(m)
            if system_text and merged:
                merged[0] = {"role": merged[0]["role"], "content": system_text.strip() + "\n\n" + merged[0].get("content", "")}
            messages = merged or messages

            prompt = messages[-1].get("content", "") if messages else ""
            conversation = [
                {"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in messages[:-1]
                if m.get("role") in ("user", "assistant")
            ]

            payload = {
                "chat_session_id": session_id,
                "prompt": prompt,
                "parent_message_id": None,
                "model": model,
                "temperature": temperature,
                "stream": stream,
                "ref_file_ids": [],
            }
            if conversation:
                payload["conversation"] = conversation

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "x-app-version": "2.0.0",
                "x-client-locale": "en_US",
                "x-client-platform": "web",
                "x-client-timezone-offset": "3600",
                "x-client-version": "2.0.0",
                "Referer": "https://chat.deepseek.com/",
                "Origin": "https://chat.deepseek.com",
            }
            if pow_response:
                headers["x-ds-pow-response"] = pow_response

            if stream:
                return self._stream_response(payload, headers)

            response = self.session.post(
                f"{self.api_url}/chat/completion",
                json=payload,
                headers=headers,
                stream=True,
                timeout=120,
            )

            if response.status_code != 200:
                return {"error": f"API error: {response.status_code} - {response.text[:200]}"}

            # DeepSeek always streams — collect content from patch chunks
            content = ""
            usage = {}
            for line in response.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                if not line_str.startswith("data: "):
                    continue
                try:
                    chunk = json.loads(line_str[6:])
                except json.JSONDecodeError:
                    continue
                # error chunk
                if chunk.get("code") not in (0, None):
                    if chunk.get("code") in [40003, 40001]:
                        self._clear_credentials()
                    return {"error": chunk.get("msg", "Unknown API error")}
                p = chunk.get("p", "")
                o = chunk.get("o", "")
                v = chunk.get("v")
                # content delta: no "p" key (short token) or first fragment append
                if "p" not in chunk and isinstance(v, str):
                    content += v
                elif "p" not in chunk and isinstance(v, dict):
                    # initial chunk: v.response.fragments[0].content has first token
                    first = (v.get("response", {}).get("fragments") or [{}])[0].get("content", "")
                    if first:
                        content += first
                elif p == "response/fragments/-1/content" and o == "APPEND" and isinstance(v, str):
                    content += v
                elif p == "response" and o == "BATCH" and isinstance(v, list):
                    for patch in v:
                        if patch.get("p") == "accumulated_token_usage":
                            usage["total_tokens"] = patch.get("v", 0)

            return {
                "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
            }

        except Exception as e:
            logger.exception("Chat completion error")
            error_msg = {"error": str(e)}
            return self._error_stream(error_msg) if stream else error_msg

    async def _error_stream(self, error_msg: dict):
        yield f"data: {json.dumps(error_msg)}\n\n"
        yield "data: [DONE]\n\n"

    async def _stream_response(self, payload: dict, headers: dict):  # noqa: E501
        try:
            with self.session.post(
                f"{self.api_url}/chat/completion",
                json=payload,
                headers=headers,
                stream=True,
                timeout=120,
            ) as response:
                if response.status_code != 200:
                    yield f"data: {json.dumps({'error': f'HTTP {response.status_code}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                msg_id = f"chatcmpl-stream"
                import time as _time
                created = int(_time.time())
                model = payload.get("model", "deepseek-chat")

                for line in response.iter_lines():
                    if not line:
                        continue
                    line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                    if not line_str.startswith("data: "):
                        continue
                    try:
                        chunk = json.loads(line_str[6:])
                    except json.JSONDecodeError:
                        continue

                    p = chunk.get("p", "")
                    o = chunk.get("o", "")
                    v = chunk.get("v")

                    delta_text = None
                    if "p" not in chunk and isinstance(v, str):
                        delta_text = v
                    elif "p" not in chunk and isinstance(v, dict):
                        first = (v.get("response", {}).get("fragments") or [{}])[0].get("content", "")
                        if first:
                            delta_text = first
                    elif p == "response/fragments/-1/content" and o == "APPEND" and isinstance(v, str):
                        delta_text = v
                    elif p == "response/status" and v in ("FINISHED", "INCOMPLETE"):
                        finish_reason = "stop" if v == "FINISHED" else "length"
                        finish = {"id": msg_id, "object": "chat.completion.chunk", "created": created,
                                  "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]}
                        yield f"data: {json.dumps(finish)}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    if delta_text is not None:
                        sse = {"id": msg_id, "object": "chat.completion.chunk", "created": created,
                               "model": model, "choices": [{"index": 0, "delta": {"content": delta_text}, "finish_reason": None}]}
                        yield f"data: {json.dumps(sse)}\n\n"

                yield "data: [DONE]\n\n"
        except Exception as e:
            logger.exception("Stream error")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    def get_status(self) -> AuthStatus:
        creds = self._get_stored_credentials()
        if not creds:
            return AuthStatus(is_authenticated=False)
        token = creds.get("access_token")
        if not token:
            return AuthStatus(is_authenticated=False)
        expires_at = self._parse_datetime(creds.get("token_expires_at"))
        is_expired = expires_at and self._get_current_time() >= expires_at - timedelta(minutes=5)
        return AuthStatus(
            is_authenticated=not is_expired,
            email=creds.get("email"),
            token_expires_at=expires_at,
            last_login=self._parse_datetime(creds.get("last_login")),
            error="Token expired" if is_expired else None,
        )
