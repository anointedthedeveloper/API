import json
import sqlite3
import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, AsyncGenerator
from dataclasses import dataclass

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


@dataclass
class AuthStatus:
    is_authenticated: bool
    email: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    error: Optional[str] = None


class DeepSeekManager:
    """Manages DeepSeek API authentication and chat completions."""
    
    def __init__(self, db_path: str = "deepseek_credentials.db"):
        self.db_path = db_path
        self.base_url = "https://chat.deepseek.com"
        self.api_url = "https://chat.deepseek.com/api/v0"
        self.session = requests.Session()
        
        # Set comprehensive browser headers to avoid detection
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://chat.deepseek.com/',
            'Origin': 'https://chat.deepseek.com',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
        })
        
        self._init_database()
    
    def _init_database(self) -> None:
        """Initialize SQLite database for storing credentials."""
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
        logger.info("Database initialized at %s", self.db_path)
    
    def _extract_token_string(self, token_input) -> Optional[str]:
        """
        Extract token string from various formats:
        - String directly: "CZAQ71Ri5E2j8kxvpE3chb6q8bcySt7MygB8L2n1LLIjKYrkcFbLbvC41ZGcqrrj"
        - Object with value: {"value": "...", "__version": "0"}
        - JSON string: '{"value": "...", "__version": "0"}'
        """
        if not token_input:
            return None
        
        # If it's already a string
        if isinstance(token_input, str):
            # Check if it's a JSON string representing an object
            try:
                parsed = json.loads(token_input)
                if isinstance(parsed, dict) and "value" in parsed:
                    token = parsed["value"]
                    logger.info("Extracted token from JSON string (length: %d)", len(token))
                    return token
                elif isinstance(parsed, dict) and "token" in parsed:
                    token = parsed["token"]
                    logger.info("Extracted token from JSON string (length: %d)", len(token))
                    return token
            except json.JSONDecodeError:
                # It's a plain token string
                if len(token_input) > 50:  # Likely a valid token
                    logger.info("Using plain token string (length: %d)", len(token_input))
                    return token_input
                else:
                    logger.warning("Token seems too short: %d characters", len(token_input))
                    return token_input
        
        # If it's a dict with value field (this handles your token format)
        if isinstance(token_input, dict):
            if "value" in token_input:
                token = token_input["value"]
                logger.info("Extracted token from object with value field (length: %d)", len(token))
                return token
            # Also check for direct token field
            if "token" in token_input:
                token = token_input["token"]
                logger.info("Extracted token from token field (length: %d)", len(token))
                return token
        
        # If it's bytes
        if isinstance(token_input, bytes):
            try:
                token_str = token_input.decode('utf-8')
                return self._extract_token_string(token_str)
            except:
                pass
        
        logger.warning("Could not extract token from type: %s", type(token_input))
        return None
    
    def _parse_datetime(self, dt_str: Optional[str]) -> Optional[datetime]:
        """Parse datetime string to datetime object."""
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        except:
            return None
    
    def _format_datetime(self, dt: datetime) -> str:
        """Format datetime to ISO string."""
        return dt.isoformat()
    
    def _get_current_time(self) -> datetime:
        """Get current UTC time."""
        return datetime.utcnow()
    
    def _store_credentials(self, email: str, access_token: str, refresh_token: str, expires_in: int) -> None:
        expires_at = self._get_current_time() + timedelta(seconds=expires_in)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO credentials 
                (id, email, access_token, refresh_token, token_expires_at, last_login)
                VALUES (1, ?, ?, ?, ?, ?)
            """, (email, access_token, refresh_token, self._format_datetime(expires_at), self._format_datetime(self._get_current_time())))
            conn.commit()
        logger.info("Credentials stored for user: %s (expires: %s)", email, expires_at)
    
    def _get_stored_credentials(self) -> Optional[Dict[str, Any]]:
        """Get stored credentials from database."""
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
        logger.info("Credentials cleared")
    
    async def login_with_email(self, email: str, password: str) -> AuthStatus:
        """Login with email and password."""
        logger.info("Attempting email login for: %s", email)
        
        try:
            # First, try to get CSRF token if needed
            self.session.get(f"{self.base_url}/", timeout=10)
            
            response = self.session.post(
                f"{self.api_url}/users/login",
                json={"email": email, "password": password},
                timeout=30
            )
            
            logger.info("Login response status: %d", response.status_code)
            logger.debug("Login response: %s", response.text[:500])
            
            if response.status_code != 200:
                error_msg = f"Login failed: HTTP {response.status_code}"
                logger.error(error_msg)
                return AuthStatus(is_authenticated=False, error=error_msg)
            
            data = response.json()
            logger.debug("Response data keys: %s", data.keys())
            
            # Check response code
            if data.get("code") != 0 and data.get("code") is not None:
                error_msg = data.get("msg", "Login failed")
                logger.error("API error: %s", error_msg)
                return AuthStatus(is_authenticated=False, error=error_msg)
            
            # Extract token from various response formats
            token = None
            response_data = data.get("data", {})
            
            if "token" in response_data:
                token = response_data["token"]
            elif "access_token" in response_data:
                token = response_data["access_token"]
            elif "value" in response_data:
                token = response_data["value"]
            elif "token" in data:
                token = data["token"]
            
            if not token:
                logger.error("No token found in response")
                logger.debug("Full response: %s", json.dumps(data, indent=2))
                return AuthStatus(is_authenticated=False, error="No token in response")
            
            # Extract string if it's an object
            token = self._extract_token_string(token)
            
            if not token:
                return AuthStatus(is_authenticated=False, error="Invalid token format")
            
            logger.info("Successfully obtained token (length: %d)", len(token))
            
            # Get user info to verify
            user_info = await self._get_user_info(token)
            if user_info:
                actual_email = user_info.get("email", email)
            else:
                actual_email = email
            
            expires_in = 86400  # 24 hours
            self._store_credentials(actual_email, token, token, expires_in)
            
            return AuthStatus(
                is_authenticated=True,
                email=actual_email,
                token_expires_at=self._get_current_time() + timedelta(seconds=expires_in),
                last_login=self._get_current_time()
            )
            
        except requests.exceptions.Timeout:
            logger.error("Login timeout")
            return AuthStatus(is_authenticated=False, error="Login timeout - please try again")
        except Exception as e:
            logger.exception("Login error")
            return AuthStatus(is_authenticated=False, error=f"Login error: {str(e)}")
    
    async def _get_user_info(self, token: str) -> Optional[Dict]:
        """Get user information using token."""
        try:
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            response = self.session.post(
                f"{self.api_url}/users/current",
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("code") == 0:
                    return data.get("data", {})
            
            # Try without Bearer
            headers = {"Authorization": token}
            response = self.session.post(
                f"{self.api_url}/users/current",
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("code") == 0:
                    return data.get("data", {})
            
            return None
        except Exception as e:
            logger.error("Error getting user info: %s", str(e))
            return None
    
    async def login_with_manual_token(self, token_input) -> AuthStatus:
        """Login with manual token from browser LocalStorage."""
        # Extract the actual token string from whatever format was pasted
        token = self._extract_token_string(token_input)
        
        if not token:
            logger.error("Could not extract token from input")
            return AuthStatus(is_authenticated=False, error="Invalid token format. Please paste token value from localStorage.")
        
        logger.info("Validating manual token (length: %d)", len(token))
        logger.debug("Token preview: %s...", token[:50])
        
        try:
            # Validate by getting user info
            user_info = await self._get_user_info(token)
            
            if not user_info:
                error_msg = "Token validation failed - Token may be expired or invalid. Please get a fresh token from chat.deepseek.com"
                logger.error(error_msg)
                return AuthStatus(is_authenticated=False, error=error_msg)
            
            email = user_info.get("email", "manual_token_user")
            logger.info("Token validated successfully for user: %s", email)
            
            # Store token with longer expiry (7 days as it's a refresh token style)
            expires_in = 604800  # 7 days for manual token
            self._store_credentials(email, token, token, expires_in)
            
            return AuthStatus(
                is_authenticated=True,
                email=email,
                token_expires_at=self._get_current_time() + timedelta(seconds=expires_in),
                last_login=self._get_current_time()
            )
            
        except requests.exceptions.Timeout:
            logger.error("Token validation timeout")
            return AuthStatus(is_authenticated=False, error="Request timeout")
        except Exception as e:
            logger.exception("Token validation error")
            return AuthStatus(is_authenticated=False, error=f"Token validation error: {str(e)}")
    
    async def login_with_google(self, id_token: str) -> AuthStatus:
        """Login with Google OAuth ID token."""
        logger.info("Attempting Google login with ID token (length: %d)", len(id_token))
        
        try:
            # DeepSeek's Google OAuth endpoint
            response = self.session.post(
                f"{self.api_url}/users/oauth/google",
                json={"id_token": id_token},
                timeout=30
            )
            
            logger.info("Google login response status: %d", response.status_code)
            logger.debug("Response body: %s", response.text[:300])
            
            if response.status_code != 200:
                error_msg = f"Google login failed: HTTP {response.status_code}"
                logger.error(error_msg)
                return AuthStatus(is_authenticated=False, error=error_msg)
            
            data = response.json()
            
            if data.get("code") != 0:
                error_msg = data.get("msg", "Google login failed")
                logger.error(error_msg)
                return AuthStatus(is_authenticated=False, error=error_msg)
            
            # Extract token
            token = data.get("data", {}).get("token")
            if not token:
                logger.error("No token in Google response")
                return AuthStatus(is_authenticated=False, error="No token in response")
            
            user_data = data.get("data", {})
            email = user_data.get("email", f"google_user_{int(time.time())}")
            
            expires_in = 86400
            self._store_credentials(email, token, token, expires_in)
            
            logger.info("Google login successful for: %s", email)
            
            return AuthStatus(
                is_authenticated=True,
                email=email,
                token_expires_at=self._get_current_time() + timedelta(seconds=expires_in),
                last_login=self._get_current_time()
            )
            
        except Exception as e:
            logger.exception("Google login error")
            return AuthStatus(is_authenticated=False, error=f"Google login error: {str(e)}")
    
    async def _refresh_token(self) -> bool:
        """Refresh access token."""
        try:
            creds = self._get_stored_credentials()
            if not creds:
                logger.warning("No credentials found for refresh")
                return False
            
            token = creds.get("access_token")
            if not token:
                logger.warning("No access token for refresh")
                return False
            
            # Try to refresh by getting current user (this might refresh the token)
            user_info = await self._get_user_info(token)
            
            if user_info:
                # Token is still valid
                logger.info("Token is still valid")
                return True
            
            logger.warning("Token refresh failed - token may be expired")
            return False
            
        except Exception as e:
            logger.exception("Token refresh error")
            return False
    
    async def get_valid_token(self) -> Optional[str]:
        """Get valid access token, refreshing if necessary."""
        creds = self._get_stored_credentials()
        if not creds:
            logger.debug("No credentials found")
            return None
        
        token = creds.get("access_token")
        if not token:
            logger.debug("No access token in credentials")
            return None
        
        # Check if token is expired
        expires_at = self._parse_datetime(creds.get("token_expires_at"))
        if expires_at and self._get_current_time() >= expires_at - timedelta(minutes=5):
            logger.info("Token expired or expiring soon, attempting refresh")
            if await self._refresh_token():
                creds = self._get_stored_credentials()
                if creds:
                    return creds.get("access_token")
            logger.warning("Token refresh failed, token may be invalid")
            return None
        
        # Validate token is still working
        user_info = await self._get_user_info(token)
        if not user_info:
            logger.warning("Token validation failed, attempting refresh")
            if await self._refresh_token():
                creds = self._get_stored_credentials()
                if creds:
                    return creds.get("access_token")
            return None
        
        logger.debug("Valid token found, expires at %s", expires_at)
        return token
    
    async def _create_chat_session(self) -> Optional[str]:
        """Create a new chat session."""
        try:
            token = await self.get_valid_token()
            if not token:
                logger.error("No valid token for session creation")
                return None
            
            headers = {"Authorization": f"Bearer {token}"}
            
            response = self.session.post(
                f"{self.api_url}/chat_session/create",
                headers=headers,
                json={},
                timeout=30
            )
            
            # Try without Bearer if first attempt fails
            if response.status_code in [401, 403]:
                logger.debug("Retrying session creation without Bearer prefix")
                headers = {"Authorization": token}
                response = self.session.post(
                    f"{self.api_url}/chat_session/create",
                    headers=headers,
                    json={},
                    timeout=30
                )
            
            logger.debug("Session creation response status: %d", response.status_code)
            
            if response.status_code != 200:
                logger.error("Failed to create session: HTTP %d", response.status_code)
                logger.debug("Response: %s", response.text[:200])
                return None
            
            data = response.json()
            
            if data.get("code") != 0:
                logger.error("API error creating session: %s", data.get("msg"))
                return None
            
            session_id = data.get("data", {}).get("id")
            if not session_id:
                session_id = data.get("id")
            
            if session_id:
                logger.info("Created chat session: %s", session_id)
            else:
                logger.warning("No session ID in response")
                logger.debug("Response: %s", json.dumps(data, indent=2))
            
            return session_id
            
        except Exception as e:
            logger.exception("Session creation error")
            return None
    
    async def chat_completion(
        self, 
        messages: List[Dict[str, str]], 
        stream: bool = False,
        temperature: float = 0.7,
        model: str = "deepseek-chat"
    ):
        """Send chat completion request."""
        token = await self.get_valid_token()
        if not token:
            error_msg = {"error": "Not authenticated. Please login first."}
            logger.error("Chat completion attempted without authentication")
            if stream:
                return self._error_stream(error_msg)
            return error_msg
        
        session_id = await self._create_chat_session()
        if not session_id:
            error_msg = {"error": "Failed to create chat session"}
            logger.error("Failed to create chat session")
            if stream:
                return self._error_stream(error_msg)
            return error_msg
        
        try:
            # Extract the last user message
            last_message = messages[-1] if messages else {"content": ""}
            prompt = last_message.get("content", "")
            
            # Build conversation history for context
            conversation = []
            for msg in messages[:-1]:  # Exclude the last message as it's the current prompt
                conversation.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", "")
                })
            
            payload = {
                "chat_session_id": session_id,
                "prompt": prompt,
                "parent_message_id": None,
                "model": model,
                "temperature": temperature,
                "stream": stream
            }
            
            # Add conversation history if provided
            if conversation:
                payload["conversation"] = conversation
            
            headers = {"Authorization": f"Bearer {token}"}
            
            logger.info("Sending chat request (stream=%s, model=%s)", stream, model)
            logger.debug("Prompt length: %d characters", len(prompt))
            
            if stream:
                return self._stream_response(payload, headers, session_id)
            else:
                response = self.session.post(
                    f"{self.api_url}/chat/completion",
                    json=payload,
                    headers=headers,
                    timeout=120
                )
                
                logger.debug("Chat completion response status: %d", response.status_code)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if data.get("code") != 0:
                        error_msg = data.get("msg", "Unknown API error")
                        logger.error("API error: %s", error_msg)
                        return {"error": error_msg}
                    
                    response_data = data.get("data", {})
                    content = response_data.get("answer", "")
                    
                    if not content:
                        # Try alternative response formats
                        content = response_data.get("message", {}).get("content", "")
                        if not content:
                            content = response_data.get("content", "")
                    
                    logger.info("Received response (length: %d characters)", len(content))
                    
                    return {
                        "choices": [{
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop"
                        }],
                        "usage": {
                            "prompt_tokens": response_data.get("prompt_tokens", 0),
                            "completion_tokens": response_data.get("completion_tokens", 0),
                            "total_tokens": response_data.get("total_tokens", 0)
                        }
                    }
                else:
                    logger.error("API error: HTTP %d", response.status_code)
                    logger.debug("Response: %s", response.text[:500])
                    return {"error": f"API error: {response.status_code} - {response.text[:200]}"}
                    
        except Exception as e:
            logger.exception("Chat completion error")
            error_msg = {"error": str(e)}
            if stream:
                return self._error_stream(error_msg)
            return error_msg
    
    async def _error_stream(self, error_msg: dict):
        """Generate error stream."""
        yield f"data: {json.dumps(error_msg)}\n\n"
        yield "data: [DONE]\n\n"
    
    async def _stream_response(self, payload: dict, headers: dict, session_id: str):
        """Handle streaming response."""
        try:
            with self.session.post(
                f"{self.api_url}/chat/completion",
                json=payload,
                headers=headers,
                stream=True,
                timeout=120
            ) as response:
                if response.status_code != 200:
                    error_msg = f"HTTP {response.status_code}"
                    logger.error("Stream error: %s", error_msg)
                    yield f"data: {json.dumps({'error': error_msg})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                
                logger.info("Streaming response started")
                chunk_count = 0
                
                for line in response.iter_lines():
                    if line:
                        line_str = line.decode('utf-8') if isinstance(line, bytes) else line
                        if line_str.startswith('data: '):
                            chunk_count += 1
                            yield f"{line_str}\n\n"
                        elif line_str == 'data: [DONE]':
                            yield "data: [DONE]\n\n"
                            break
                
                logger.info("Streaming completed, sent %d chunks", chunk_count)
                        
        except Exception as e:
            logger.exception("Stream error")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield "data: [DONE]\n\n"
    
    def get_status(self) -> AuthStatus:
        """Get current authentication status."""
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
            error="Token expired" if is_expired else None
        )