# routes/accounts.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from routes.auth import get_current_user
from database.crud import get_accounts, add_account, delete_account
from services.twitter_crawler import test_account_and_proxy

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

class AccountCreate(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    email: str = Field(..., min_length=1)
    proxy: str = Field("", description="格式: http://user:pass@ip:port")
    remark: str = Field("")

@router.get("", dependencies=[Depends(get_current_user)])
async def list_accounts():
    """
    获取账号池列表
    """
    return get_accounts()

@router.post("", dependencies=[Depends(get_current_user)])
async def create_or_update_account(data: AccountCreate):
    """
    新增或修改账号配置。
    保存时会默认进行双重验证：即测试代理连通性以及 X 登录凭证。
    """
    # 调用服务执行登录与代理校验，并获取由 Token 自动解析的推特用户名
    success, result = await test_account_and_proxy(
        username=data.username,
        password=data.password,
        email=data.email,
        proxy=data.proxy
    )
    
    if not success:
        # 如果测试不通过，result 是错误信息，直接报错抛给前端展示
        raise HTTPException(status_code=400, detail=result)
        
    # 验证通过，result 是自动检测到的真实用户名，入库保存
    detected_username = result
    add_account(
        username=detected_username,
        password=data.password, # 存储 auth_token
        email=data.email,      # 虚拟 dummy 邮箱
        proxy=data.proxy,
        remark=data.remark
    )
    return {"message": f"成功识别并验证推特账号 @{detected_username}！配置已保存。"}

@router.delete("/{username}", dependencies=[Depends(get_current_user)])
async def remove_account(username: str):
    """
    从池子删除账号
    """
    delete_account(username)
    return {"message": "账号已删除"}
