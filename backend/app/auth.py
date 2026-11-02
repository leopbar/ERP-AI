import base64
import hashlib
import hmac
import os
import secrets
import time
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

class Login(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ":" + hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200000).hex()

def secret():
    value = os.getenv("SESSION_SECRET", "")
    if len(value) < 32:
        raise HTTPException(503, "Configure SESSION_SECRET com pelo menos 32 caracteres")
    return value.encode()

def session_token():
    body = base64.urlsafe_b64encode(f"{int(time.time()) + 28800}:{secrets.token_hex(16)}".encode()).decode()
    return body + "." + hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest()

def require_user(request: Request):
    token = request.cookies.get("erp_session", "")
    try:
        body, signature = token.split(".")
        valid = hmac.compare_digest(signature, hmac.new(secret(), body.encode(), hashlib.sha256).hexdigest())
        expires = int(base64.urlsafe_b64decode(body).decode().split(":")[0])
        if not valid or expires < time.time():
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(401, "Entre para continuar")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        check_origin(request)
    return os.getenv("CONSULTANT_USER", "consultor")

def check_origin(request):
    allowed = os.getenv("APP_ORIGIN", "http://localhost:3001")
    if request.headers.get("origin") != allowed:
        raise HTTPException(403, "Origem da operação não autorizada")
