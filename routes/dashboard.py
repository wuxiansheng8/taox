# routes/dashboard.py
from fastapi import APIRouter, Depends
from routes.auth import get_current_user
from database.crud import get_metrics_24h, get_settings
from services.scheduler import SCHEDULER_STATE

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

@router.get("/metrics", dependencies=[Depends(get_current_user)])
async def get_dashboard_metrics():
    """
    获取后台实时看板数据
    """
    settings = get_settings()
    tg_configured = bool(settings.get("tg_token") and settings.get("tg_chat_id"))
    
    # 获取过去24小时每小时成功/失败分布
    stats_24h = get_metrics_24h()
    
    return {
        "is_running": SCHEDULER_STATE["is_running"],
        "next_scan_time": SCHEDULER_STATE["next_scan_time"],
        "active_accounts_count": SCHEDULER_STATE["active_accounts_count"],
        "last_error": SCHEDULER_STATE["last_error"],
        "tg_configured": tg_configured,
        "chart_data": stats_24h
    }
