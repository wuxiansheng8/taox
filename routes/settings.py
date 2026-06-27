# routes/settings.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from routes.auth import get_current_user, hash_password
from database.crud import get_settings, update_settings
from services.telegram_bot import test_telegram_connection
from services.translator import test_translation, check_openai_balance
from services.scheduler import start_scheduler, stop_scheduler, SCHEDULER_STATE

router = APIRouter(prefix="/api/settings", tags=["settings"])

class SettingsUpdate(BaseModel):
    admin_username: str = Field("admin")
    admin_password: str = Field("", description="为空则不修改密码")
    
    tg_token: str = Field("")
    tg_chat_id: str = Field("")
    polling_interval: int = Field(10, ge=5) # 频率最小限制在 5 秒以上
    twitter_list_id: str = Field("")
    
    translate_enabled: int = Field(0, description="0禁用，1启用")
    translate_provider_primary: str = Field("google")
    translate_provider_backup: str = Field("google")
    
    openai_primary_api_key: str = Field("")
    openai_primary_base_url: str = Field("")
    openai_primary_model: str = Field("")
    
    openai_backup_api_key: str = Field("")
    openai_backup_base_url: str = Field("")
    openai_backup_model: str = Field("")

class TestTGRequest(BaseModel):
    token: str
    chat_id: str

class TestTranslationRequest(BaseModel):
    channel: str # "primary" 或 "backup"

@router.get("", dependencies=[Depends(get_current_user)])
async def get_all_settings():
    """
    获取全局配置（隐藏敏感密码 Hash）
    """
    settings = get_settings()
    # 隐藏密码 Hash
    if "admin_password_hash" in settings:
        del settings["admin_password_hash"]
    return settings

@router.post("", dependencies=[Depends(get_current_user)])
async def save_settings(data: SettingsUpdate):
    """
    保存全局设置。如修改了密码，会重新加密入库。
    """
    settings_dict = data.model_dump()
    
    # 特殊处理密码
    password = settings_dict.pop("admin_password")
    if password.strip():
        settings_dict["admin_password_hash"] = hash_password(password.strip())
        
    # 保存入库
    update_settings(settings_dict)
    
    # 动态重启/更新调度状态
    if SCHEDULER_STATE["is_running"]:
        await stop_scheduler()
        await start_scheduler()
        
    return {"message": "全局配置已保存"}

@router.post("/test-tg", dependencies=[Depends(get_current_user)])
async def api_test_tg(data: TestTGRequest):
    """
    一键测试 Telegram 连通性
    """
    success = await test_telegram_connection(data.token, data.chat_id)
    if not success:
        raise HTTPException(status_code=400, detail="Telegram 连接失败，请检查 Token 和 Chat ID 是否正确")
    return {"message": "Telegram 通道连接正常！已向目标发送测试消息。"}

@router.post("/test-translation", dependencies=[Depends(get_current_user)])
async def api_test_translation(data: TestTranslationRequest):
    """
    一键测试 AI 翻译配置连通性
    """
    settings = get_settings()
    ch = data.channel
    
    if ch == "primary":
        provider = settings.get("translate_provider_primary", "google")
        config = {
            "api_key": settings.get("openai_primary_api_key"),
            "base_url": settings.get("openai_primary_base_url"),
            "model": settings.get("openai_primary_model")
        }
    else:
        provider = settings.get("translate_provider_backup", "google")
        config = {
            "api_key": settings.get("openai_backup_api_key"),
            "base_url": settings.get("openai_backup_base_url"),
            "model": settings.get("openai_backup_model")
        }
        
    success, res_msg = await test_translation(provider, config)
    if not success:
        raise HTTPException(status_code=400, detail=f"翻译测试失败: {res_msg}")
    return {"message": res_msg}

@router.post("/check-balance", dependencies=[Depends(get_current_user)])
async def api_check_balance(data: TestTranslationRequest):
    """
    一键查询翻译通道额度余额
    """
    settings = get_settings()
    ch = data.channel
    
    if ch == "primary":
        provider = settings.get("translate_provider_primary", "google")
        api_key = settings.get("openai_primary_api_key")
        base_url = settings.get("openai_primary_base_url")
    else:
        provider = settings.get("translate_provider_backup", "google")
        api_key = settings.get("openai_backup_api_key")
        base_url = settings.get("openai_backup_base_url")
        
    if provider == "google":
        return {"balance": "谷歌免费翻译：无需充值额度。"}
        
    balance_info = await check_openai_balance(api_key, base_url)
    return {"balance": balance_info}

@router.post("/toggle-scheduler", dependencies=[Depends(get_current_user)])
async def toggle_scheduler(action: str):
    """
    开启或关闭推特扫描任务
    """
    if action == "start":
        await start_scheduler()
        return {"message": "扫描器已启动"}
    elif action == "stop":
        await stop_scheduler()
        return {"message": "扫描器已暂停"}
    else:
        raise HTTPException(status_code=400, detail="未知的控制指令")
