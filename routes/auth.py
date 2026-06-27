# routes/auth.py
from fastapi import APIRouter, Response, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import hashlib
import uuid
from database.crud import get_setting

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 在内存中存储活动的 Session ID
ACTIVE_SESSIONS = set()

class LoginRequest(BaseModel):
    username: str
    password: str

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def get_current_user(request: Request):
    """
    依赖注入：验证请求中的 Session Cookie
    """
    session_token = request.cookies.get("session_token")
    if not session_token or session_token not in ACTIVE_SESSIONS:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return "admin"

@router.post("/login")
async def login(response: Response, data: LoginRequest):
    admin_user = get_setting("admin_username", "admin")
    admin_pw_hash = get_setting("admin_password_hash")
    
    if data.username != admin_user:
        raise HTTPException(status_code=400, detail="用户名或密码错误")
        
    input_hash = hash_password(data.password)
    if input_hash != admin_pw_hash:
        raise HTTPException(status_code=400, detail="用户名或密码错误")
        
    # 登录成功，分配 Session
    session_token = str(uuid.uuid4())
    ACTIVE_SESSIONS.add(session_token)
    
    # 将 Session 写入 HttpOnly Cookie 中以保障安全
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        max_age=86400 * 7, # 7天免登
        samesite="lax"
    )
    return {"message": "登录成功"}

@router.post("/logout")
async def logout(request: Request, response: Response):
    session_token = request.cookies.get("session_token")
    if session_token in ACTIVE_SESSIONS:
        ACTIVE_SESSIONS.remove(session_token)
    response.delete_cookie("session_token")
    return {"message": "退出成功"}

@router.get("/check")
async def check_auth(request: Request):
    session_token = request.cookies.get("session_token")
    if session_token and session_token in ACTIVE_SESSIONS:
        return {"authenticated": True, "username": get_setting("admin_username", "admin")}
    return {"authenticated": False}
