import asyncio
import json
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from deepseek_manager import DeepSeekManager, AuthStatus


class EmailLoginRequest(BaseModel):
    email: str
    password: str


class TokenLoginRequest(BaseModel):
    token: str


class GoogleLoginRequest(BaseModel):
    id_token: str


class ChatCompletionRequest(BaseModel):
    messages: List[Dict[str, str]]
    model: str = "deepseek-chat"
    temperature: float = 0.7
    stream: bool = False


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Dict[str, Any]]
    usage: Optional[Dict[str, int]] = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[Dict[str, Any]]


app = FastAPI(title="DeepSeek API Server", version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize DeepSeek manager
manager = DeepSeekManager()


def get_local_ip() -> str:
    """Get local IP address for network access."""
    try:
        # Create a socket to get local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


def print_banner(host: str, port: int) -> None:
    """Print beautiful startup banner."""
    local_ip = get_local_ip()
    
    banner = f"""
╔══════════════════════════════════════════════════════════════════╗
║                    🚀 DeepSeek API Server                        ║
╠══════════════════════════════════════════════════════════════════╣
║  Status:     Running                                             ║
║  Host:       {host:<20} Port: {port:<20} ║
║  Started:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):<20} ║
╠══════════════════════════════════════════════════════════════════╣
║  📍 Local Access:                                                ║
║     http://localhost:{port}                                        ║
╠══════════════════════════════════════════════════════════════════╣
║  🌐 Network Access (WiFi/LAN):                                   ║
║     http://{local_ip}:{port}                                      ║
╠══════════════════════════════════════════════════════════════════╣
║  🔧 API Endpoints:                                               ║
║     GET  /api/status        - Authentication status              ║
║     POST /api/login/email   - Email/password login               ║
║     POST /api/login/google  - Google OAuth login                  ║
║     POST /api/login/token   - Manual token login                 ║
║     POST /v1/chat/completions - OpenAI-compatible chat API       ║
║     GET  /health           - Health check                        ║
╠══════════════════════════════════════════════════════════════════╣
║  📖 Example Usage:                                               ║
║     curl http://localhost:{port}/v1/chat/completions \\           ║
║          -H "Content-Type: application/json" \\                  ║
║          -d '{{"messages": [{{"role": "user", "content": "Hello"}}]}}' ║
╚══════════════════════════════════════════════════════════════════╝
"""
    print(banner)


def open_browser_delayed(url: str, delay: int = 2) -> None:
    """Open browser after delay."""
    time.sleep(delay)
    try:
        webbrowser.open(url)
    except:
        pass


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve setup HTML page."""
    setup_file = Path(__file__).parent / "setup.html"
    if setup_file.exists():
        return HTMLResponse(content=setup_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>DeepSeek API Server</h1><p>Setup page not found.</p>")


@app.get("/api/status")
async def get_status():
    """Get authentication status."""
    status = manager.get_status()
    return {
        "authenticated": status.is_authenticated,
        "email": status.email,
        "token_expires_at": status.token_expires_at.isoformat() if status.token_expires_at else None,
        "last_login": status.last_login.isoformat() if status.last_login else None,
        "error": status.error
    }


@app.post("/api/login/email")
async def login_with_email(request: EmailLoginRequest):
    """Login with email and password."""
    status = await manager.login_with_email(request.email, request.password)
    return {
        "success": status.is_authenticated,
        "email": status.email,
        "error": status.error
    }


@app.post("/api/login/token")
async def login_with_token(request: TokenLoginRequest):
    """Login with manual token."""
    status = await manager.login_with_manual_token(request.token)
    return {
        "success": status.is_authenticated,
        "email": status.email,
        "error": status.error
    }


@app.post("/api/login/google")
async def login_with_google(request: GoogleLoginRequest):
    """Login with Google OAuth."""
    status = await manager.login_with_google(request.id_token)
    return {
        "success": status.is_authenticated,
        "email": status.email,
        "error": status.error
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """OpenAI-compatible chat completions endpoint."""
    if request.stream:
        gen = await manager.chat_completion(
            messages=request.messages,
            stream=True,
            temperature=request.temperature,
            model=request.model,
        )
        async def stream_response():
            async for chunk in gen:
                yield chunk
        return StreamingResponse(
            stream_response(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    response = await manager.chat_completion(
        messages=request.messages,
        stream=False,
        temperature=request.temperature,
        model=request.model
    )

    if "error" in response:
        raise HTTPException(status_code=400, detail=response["error"])

    return ChatCompletionResponse(
        id=f"chatcmpl-{int(time.time())}",
        created=int(time.time()),
        model=request.model,
        choices=[{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": response.get("choices", [{}])[0].get("message", {}).get("content", "")
            },
            "finish_reason": response.get("choices", [{}])[0].get("finish_reason", "stop")
        }],
        usage=response.get("usage")
    )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Run the server with banner and browser auto-open."""
    print_banner(host, port)
    
    # Open browser in background thread
    browser_thread = threading.Thread(
        target=open_browser_delayed,
        args=(f"http://localhost:{port}", 2),
        daemon=True
    )
    browser_thread.start()
    
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        print("\n👋 Server stopped gracefully.")


if __name__ == "__main__":
    run_server()
