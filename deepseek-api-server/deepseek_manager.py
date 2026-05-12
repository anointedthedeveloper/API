import json
import sqlite3
import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, AsyncGenerator
from dataclasses import dataclass

# Setup logging
logging.basicConfig(level=logging.INFO)
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
    
    def _get_current_time(self) -> datetime:
        """Get current UTC time."""
        return datetime.utcnow()
    
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
    
    def _store_credentials(self, email: str, access_token: str, refresh_token: str, expires_in: int) -> None:
        expires_at = self._get_current_time() + timedelta(seconds=expires_in)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO credentials 
                (id, email, access_token, refresh_token, token_expires_at, last_login)
                VALUES (1, ?, ?, ?, ?, ?)
            """, (email, access_token, refresh_token, self._format_datetime(expires_at), self._format_datetime(self._get_current_time())))
            conn.commit()
        logger.info("Credentials stored for user: %s", email)
    
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
            # First, get challenge
            logger.debug("Fetching challenge...")
            challenge_response = self.session.post(
                f"{self.base_url}/api/v0/users/login/challenge",
                json={"email": email}
            )
            logger.debug("Challenge response status: %d", challenge_response.status_code)
            
            # Actual login
            response = self.session.post(
                f"{self.base_url}/api/v0/users/login",
                json={"email": email, "password": password},
                timeout=30
            )
            
            logger.info("Login response status: %d", response.status_code)
            logger.debug("Login response body: %s", response.text[:200])
            
            if response.status_code != 200:
                error_msg = f"Login failed: HTTP {response.status_code}"
                logger.error(error_msg)
                return AuthStatus(is_authenticated=False, error=error_msg)
            
            data = response.json()
            logger.debug("Login response JSON: %s", json.dumps(data, indent=2)[:500])
            
            # Check for token in response - DeepSeek returns token directly
            token = None
            if "data" in data and "token" in data["data"]:
                token = data["data"]["token"]
            elif "token" in data:
                token = data["token"]
            elif "access_token" in data:
                token = data["access_token"]
            elif "userToken" in data:
                token = data["userToken"]
            
            if not token:
                logger.error("No token found in response: %s", data)
                return AuthStatus(is_authenticated=False, error="No token in response")
            
            logger.info("Successfully obtained token (length: %d)", len(token))
            
            # Store credentials
            expires_in = 86400  # 24 hours for refresh token
            self._store_credentials(email, token, token, expires_in)
            
            return AuthStatus(
                is_authenticated=True,
                email=email,
                token_expires_at=self._get_current_time() + timedelta(seconds=expires_in),
                last_login=self._get_current_time()
            )
            
        except requests.exceptions.Timeout:
            logger.error("Login timeout for %s", email)
            return AuthStatus(is_authenticated=False, error="Request timeout")
        except Exception as e:
            logger.exception("Login error for %s", email)
            return AuthStatus(is_authenticated=False, error=f"Login error: {str(e)}")
    
    async def login_with_manual_token(self, token: str) -> AuthStatus:
        """Login with manual token from browser LocalStorage."""
        logger.info("Validating manual token (length: %d)", len(token))
        logger.debug("Token preview: %s...", token[:50])
        
        try:
            # Validate token by getting current user info
            # IMPORTANT: Token is raw string WITHOUT "Bearer" prefix
            headers = {
                "Authorization": token,  # Raw token, not "Bearer " + token
                "Content-Type": "application/json"
            }
            
            logger.debug("Calling /api/v0/users/current endpoint...")
            response = self.session.post(
                f"{self.base_url}/api/v0/users/current",
                headers=headers,
                timeout=30
            )
            
            logger.info("Token validation status: %d", response.status_code)
            logger.debug("Response body: %s", response.text[:300])
            
            if response.status_code != 200:
                error_msg = f"Token validation failed: HTTP {response.status_code}"
                logger.error(error_msg)
                return AuthStatus(is_authenticated=False, error=error_msg)
            
            data = response.json()
            logger.debug("Response JSON: %s", json.dumps(data, indent=2)[:500])
            
            if data.get("code") != 0:
                error_msg = data.get("msg", "Token validation failed")
                logger.error(error_msg)
                return AuthStatus(is_authenticated=False, error=error_msg)
            
            # Extract user info
            user_data = data.get("data", {})
            email = user_data.get("email") or user_data.get("user") or "manual_token_user"
            logger.info("Token validated successfully for user: %s", email)
            
            # Store with manual token
            expires_in = 3600  # Access tokens expire in ~1 hour
            self._store_credentials(email, token, "", expires_in)
            
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
                f"{self.base_url}/api/v0/users/oauth/google",
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
                logger.warning("No credentials to refresh")
                return False
            
            token = creds.get("access_token")
            if not token:
                logger.warning("No access token to refresh")
                return False
            
            headers = {"Authorization": token}
            response = self.session.post(
                f"{self.base_url}/api/v0/users/current",
                headers=headers,
                timeout=30
            )
            
            if response.status_code != 200:
                logger.warning("Token refresh failed with status %d", response.status_code)
                return False
            
            data = response.json()
            if data.get("code") != 0:
                logger.warning("Token refresh returned error code")
                return False
            
            new_token = data.get("data", {}).get("token")
            if new_token:
                self._store_credentials(creds["email"], new_token, new_token, 3600)
                logger.info("Token refreshed successfully")
                return True
            
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
            logger.info("Token expired, attempting refresh")
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
                logger.warning("No token to create session")
                return None
            
            headers = {"Authorization": token}
            response = self.session.post(
                f"{self.base_url}/api/v0/chat_session/create",
                headers=headers,
                json={},
                timeout=30
            )
            
            if response.status_code != 200:
                logger.warning("Session creation failed: %d", response.status_code)
                return None
            
            data = response.json()
            session_id = data.get("data", {}).get("id") or data.get("id")
            if session_id:
                logger.debug("Created session: %s", session_id)
                return session_id
            
            return None
            
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
            return error_msg if not stream else self._error_stream(error_msg)
        
        session_id = await self._create_chat_session()
        if not session_id:
            error_msg = {"error": "Failed to create chat session"}
            return error_msg if not stream else self._error_stream(error_msg)
        
        try:
            payload = {
                "chat_session_id": session_id,
                "prompt": messages[-1]["content"] if messages else "",
                "parent_message_id": None,
                "model": model,
                "temperature": temperature,
                "stream": stream
            }
            
            headers = {"Authorization": token}
            
            if stream:
                return self._stream_response(payload, headers)
            else:
                response = self.session.post(
                    f"{self.base_url}/api/v0/chat/completion",
                    json=payload,
                    headers=headers,
                    timeout=120
                )
                
                if response.status_code == 200:
                    data = response.json()
                    # Extract content from response
                    content = data.get("data", {}).get("answer", "")
                    if not content:
                        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    
                    return {
                        "choices": [{
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop"
                        }]
                    }
                else:
                    return {"error": f"API error: {response.status_code}"}
                    
        except Exception as e:
            logger.exception("Chat completion error")
            error_msg = {"error": str(e)}
            return error_msg if not stream else self._error_stream(error_msg)
    
    async def _error_stream(self, error_msg: dict):
        """Generate error stream."""
        yield f"data: {json.dumps(error_msg)}\n\n"
        yield "data: [DONE]\n\n"
    
    async def _stream_response(self, payload: dict, headers: dict):
        """Handle streaming response."""
        try:
            with self.session.post(
                f"{self.base_url}/api/v0/chat/completion",
                json=payload,
                headers=headers,
                stream=True,
                timeout=120
            ) as response:
                if response.status_code != 200:
                    yield f"data: {json.dumps({'error': f'HTTP {response.status_code}'})}\n\n"
                    return
                
                for line in response.iter_lines():
                    if line:
                        line_str = line.decode('utf-8') if isinstance(line, bytes) else line
                        if line_str.startswith('data: '):
                            yield f"{line_str}\n\n"
                        
        except Exception as e:
            logger.exception("Stream error")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
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
